import asyncio
import json
import logging
import random
from contextlib import closing
from datetime import date, datetime
from typing import Any

from aiogram import Bot, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from db import (
    add_sits,
    get_connection,
    get_user_display_name as db_get_user_display_name,
    get_user_sex,
    has_active_subscription,
)
from dick import update_dick_length
from group_event_engine import EVENT_COST, GroupEventEngine
from masturbate_store import MasturbateStore
from quest import update_quest_progress


logger = logging.getLogger(__name__)

STICKER_FILE_ID = "CAACAgIAAyEFAASjKavKAAIDrGi31TwpfP-R-JI64M0v6eRnTCFxAAJMUAACITxRSq0hIi2dEdhQNgQ"

PREPARE_DELAY_SEC = 10 * 60
JOIN_WINDOW_SEC = 5 * 60
FORCED_GROUP_THREAD_CHAT_ID = -1002730880821
FORCED_GROUP_THREAD_ID = 137047

GROUP_JOIN_MESSAGES = [
    "{name} пристраивается сбоку",
    "{name} садится на диван и смотрит",
    "Все немного двигаются чтобы дать {name} место",
    "{name} садится в центр круга",
    "{name} немного стесняется и активничает из-за угла",
    "Для {name} не нашлось лишнего стула, поэтому пришлось сесть на полу",
    "{name} тихонько подкрадывается и устраивается сзади",
    '{name} врывается в комнату с криком: "Я опоздал?"',
    "К всеобщей радости, {name} наконец-то с нами",
    '{name} аккуратно протискивается между диваном и столом со словами "Можно я тут?"',
    "{name} появляется с тарелкой печенья и моментально становится душой компании",
]

_store = MasturbateStore()
_engine = GroupEventEngine(_store)

_runtime_initialized = False
_outbox_task: asyncio.Task[None] | None = None
_event_flow_tasks: dict[int, asyncio.Task[None]] = {}

# ensure schema exists even if runtime init is not called yet
_store.initialize(reset_runtime_state=False)


def get_user_display_name(user_id: int, chat_id: int) -> str:
    return db_get_user_display_name(user_id, chat_id)


def log_masturbation_results(
    chat_id: int,
    participants: list[int],
    winner_id: int,
    winner_reward_sits: int,
) -> None:
    if not participants:
        return

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for uid in participants:
        is_winner = 1 if uid == winner_id else 0
        reward_sits = winner_reward_sits if uid == winner_id else 0
        rows.append((created_at, uid, chat_id, is_winner, reward_sits))

    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.executemany(
            """
            INSERT INTO masturbate_log (created_at, user_id, chat_id, is_winner, reward_sits)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


def _parse_subscription_till(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def get_subscription_mentions(chat_id: int, exclude_user_id: int) -> list[str]:
    today = date.today()
    try:
        with closing(get_connection()) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT nick, subscription_till
                FROM users
                WHERE chat_id = ? AND user_id != ?
                """,
                (chat_id, exclude_user_id),
            )
            rows = cur.fetchall()
    except Exception:
        return []

    result: list[str] = []
    for row in rows:
        subscription_till = _parse_subscription_till(row["subscription_till"])
        if not subscription_till or subscription_till < today:
            continue
        nick = (row["nick"] or "").strip()
        if not nick:
            continue
        result.append(nick)
    return result


def build_subscription_ping_text(chat_id: int, exclude_user_id: int, suffix: str) -> str | None:
    mentions = get_subscription_mentions(chat_id, exclude_user_id=exclude_user_id)
    if not mentions:
        return None
    return f"{' '.join(mentions)}\n{suffix}"


def get_winner_mention(chat_id: int, user_id: int, fallback_name: str) -> str:
    if not has_active_subscription(chat_id, user_id):
        return fallback_name

    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT nick
            FROM users
            WHERE chat_id = ? AND user_id = ?
            """,
            (chat_id, user_id),
        )
        row = cur.fetchone()

    nick = (row["nick"] or "").strip() if row else ""
    return nick or fallback_name


def _resolve_group_thread_id(chat_id: int, thread_id: int | None) -> int | None:
    if int(chat_id) == FORCED_GROUP_THREAD_CHAT_ID:
        return FORCED_GROUP_THREAD_ID
    return thread_id


def _send_kwargs_from_thread_id(chat_id: int, thread_id: int | None) -> dict[str, Any]:
    effective_thread_id = _resolve_group_thread_id(chat_id, thread_id)
    return {"message_thread_id": effective_thread_id} if effective_thread_id is not None else {}


def _enqueue_outbox_text(chat_id: int, text: str, thread_id: int | None = None) -> None:
    payload = {"text": text, "thread_id": _resolve_group_thread_id(chat_id, thread_id)}
    _store.enqueue_outbox(chat_id=chat_id, kind="send_text", payload=payload)


def _enqueue_outbox_sticker(chat_id: int, sticker: str, thread_id: int | None = None) -> None:
    payload = {"sticker": sticker, "thread_id": _resolve_group_thread_id(chat_id, thread_id)}
    _store.enqueue_outbox(chat_id=chat_id, kind="send_sticker", payload=payload)


def _enqueue_outbox_start_event_flow(chat_id: int) -> None:
    _store.enqueue_outbox(chat_id=chat_id, kind="start_event_flow", payload={})


def _record_web_chat_message(chat_id: int, message_id: int, user_id: int, text: str) -> None:
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO messages_reactions
            (chat_id, message_id, user_id, message_text, reactions_count, date)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                message_id,
                user_id,
                text,
                0,
                datetime.now().isoformat(),
            ),
        )
        conn.commit()


async def _outbox_worker(bot: Bot) -> None:
    while True:
        rows = _store.fetch_outbox_batch(limit=30)
        if not rows:
            await asyncio.sleep(0.6)
            continue

        for row in rows:
            outbox_id = int(row["id"])
            try:
                payload = json.loads(row["payload_json"])
                kind = str(row["kind"])
                chat_id = int(row["chat_id"])
                thread_id = payload.get("thread_id")
                send_kwargs = _send_kwargs_from_thread_id(chat_id, thread_id)

                if kind == "send_text":
                    text = str(payload.get("text") or "")
                    if text:
                        await bot.send_message(chat_id, text, **send_kwargs)
                elif kind == "send_html_text":
                    text = str(payload.get("text") or "")
                    disable_preview = bool(payload.get("disable_preview", True))
                    if text:
                        await bot.send_message(
                            chat_id,
                            text,
                            parse_mode="HTML",
                            disable_web_page_preview=disable_preview,
                            **send_kwargs,
                        )
                elif kind == "send_web_chat_message":
                    text = str(payload.get("text") or "").strip()
                    user_id = int(payload.get("user_id") or 0)
                    display_name = str(payload.get("display_name") or f"Игрок {user_id}").strip()
                    if text and user_id:
                        thread_id = payload.get("thread_id")
                        web_chat_send_kwargs = {"message_thread_id": int(thread_id)} if thread_id is not None else {}
                        message = await bot.send_message(chat_id, f'{display_name} (/web): "{text}"', **web_chat_send_kwargs)
                        _record_web_chat_message(chat_id, message.message_id, user_id, text)
                elif kind == "send_sticker":
                    sticker = str(payload.get("sticker") or "")
                    if sticker:
                        await bot.send_sticker(chat_id, sticker, **send_kwargs)
                elif kind == "edit_reply_markup":
                    message_id = int(payload["message_id"])
                    await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
                elif kind == "start_event_flow":
                    _schedule_event_flow(bot, chat_id)
                else:
                    _store.mark_outbox_failed(outbox_id, f"unsupported outbox kind: {kind}", terminal=True)
                    continue

                _store.mark_outbox_processed(outbox_id)
            except Exception as exc:
                attempts = int(row["attempt_count"] or 0) + 1
                terminal = attempts >= 10
                _store.mark_outbox_failed(outbox_id, str(exc), terminal=terminal)

        await asyncio.sleep(0)


async def initialize_group_runtime(bot: Bot, reset_state: bool = True) -> None:
    global _runtime_initialized, _outbox_task
    if _runtime_initialized:
        return
    _store.initialize(reset_runtime_state=reset_state)
    _outbox_task = asyncio.create_task(_outbox_worker(bot))
    if not reset_state:
        for event_row in _store.list_active_events():
            _schedule_event_flow(bot, int(event_row["chat_id"]))
    _runtime_initialized = True
    logger.info("[group] runtime initialized (reset_state=%s)", reset_state)


def join_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="Присоединиться (1 сит)", callback_data="group_join"),
        InlineKeyboardButton(text="Смотреть (бесплатно)", callback_data="group_watch"),
    )
    return kb.as_markup()


def remind_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔔 Напомнить", callback_data="group_remind"))
    return kb.as_markup()


def register_group_handlers(dp):
    @dp.callback_query(lambda c: c.data == "group_join")
    async def on_group_join(query: types.CallbackQuery):
        chat_id = query.message.chat.id
        user_id = query.from_user.id
        fallback_name = query.from_user.full_name or (f"@{query.from_user.username}" if query.from_user.username else str(user_id))
        display_name = _engine.resolve_display_name(chat_id, user_id, fallback_name)

        result = _engine.join_as_participant(
            chat_id=chat_id,
            user_id=user_id,
            display_name=display_name,
            source="tg",
        )
        if not result.ok:
            if result.code == "join_window_closed":
                await query.answer("Окно регистрации закрыто.", show_alert=True)
                return
            if result.code == "already_joined":
                await query.answer("Ты уже присоединился!", show_alert=True)
                return
            await query.answer("Ивент ещё не запущен.", show_alert=True)
            return

        if result.code == "joined_as_freebie":
            await query.answer("У вас недостаточно сита для групповой мастурбации, но мы всё запишем на камеру", show_alert=True)
            return

        await query.answer("Ты в деле!")
        phrase = random.choice(GROUP_JOIN_MESSAGES).format(name=display_name)
        _enqueue_outbox_text(chat_id=chat_id, text=phrase, thread_id=result.thread_id)
        await update_quest_progress(user_id, chat_id, "group_part", 1, bot=query.bot)

    @dp.callback_query(lambda c: c.data == "group_watch")
    async def on_group_watch(query: types.CallbackQuery):
        chat_id = query.message.chat.id
        user_id = query.from_user.id
        fallback_name = query.from_user.full_name or (f"@{query.from_user.username}" if query.from_user.username else str(user_id))
        display_name = _engine.resolve_display_name(chat_id, user_id, fallback_name)

        result = _engine.join_as_spectator(
            chat_id=chat_id,
            user_id=user_id,
            display_name=display_name,
            source="tg",
        )
        if not result.ok:
            if result.code == "join_window_closed":
                await query.answer("Окно регистрации закрыто.", show_alert=True)
                return
            if result.code == "already_joined":
                await query.answer("Ты уже зарегистрировался!", show_alert=True)
                return
            await query.answer("Ивент ещё не запущен.", show_alert=True)
            return

        _enqueue_outbox_text(
            chat_id=chat_id,
            text=f"👀 {display_name} просто посмотрит онлайн-трансляцию",
            thread_id=result.thread_id,
        )
        await query.answer("Ты в списке зрителей!")

    @dp.callback_query(lambda c: c.data == "group_remind")
    async def on_group_remind(query: types.CallbackQuery):
        chat_id = query.message.chat.id
        user_id = query.from_user.id
        display_name = query.from_user.full_name or (f"@{query.from_user.username}" if query.from_user.username else str(user_id))

        result = _engine.add_reminder(chat_id=chat_id, user_id=user_id, display_name=display_name)
        if result.ok:
            await query.answer("✅ Напомню тебе перед стартом!")
            return
        if result.code == "reminder_exists":
            await query.answer("Ты уже в списке для напоминания!", show_alert=True)
            return
        await query.answer("Ивент ещё не запущен.", show_alert=True)


async def start_group_event(message: types.Message, user_id: int):
    chat_id = message.chat.id
    thread_id = _resolve_group_thread_id(chat_id, message.message_thread_id)
    send_kwargs = _send_kwargs_from_thread_id(chat_id, thread_id)
    fallback_name = message.from_user.full_name or (f"@{message.from_user.username}" if message.from_user.username else str(user_id))
    display_name = _engine.resolve_display_name(chat_id, user_id, fallback_name)

    result = _engine.start_event(
        chat_id=chat_id,
        user_id=user_id,
        display_name=display_name,
        thread_id=thread_id,
        source="tg",
    )
    if not result.ok:
        if result.code == "insufficient_sits":
            from sosalsa import get_sits

            balance = get_sits(chat_id, user_id)
            await message.answer(f"Недостаточно сит для запуска! Нужно {EVENT_COST}, у тебя {balance}")
            return
        if result.code == "event_already_active":
            await message.answer("Ивент уже идёт, дождись окончания.")
            return
        await message.answer("Не удалось запустить ивент. Попробуй ещё раз.")
        return

    _enqueue_outbox_start_event_flow(chat_id)

    try:
        await update_quest_progress(user_id, chat_id, "group_part", 1, bot=message.bot)
    except Exception:
        logger.exception("[group] failed to update quest progress for group event start (chat_id=%s user_id=%s)", chat_id, user_id)

    subscription_ping_text = build_subscription_ping_text(
        chat_id,
        exclude_user_id=user_id,
        suffix="Сит-премиум, сбор объявлен. Если хочешь участвовать, жди открытия окна и жми кнопку.",
    )
    if subscription_ping_text:
        await message.answer(subscription_ping_text, **send_kwargs)

    _enqueue_outbox_sticker(chat_id=chat_id, sticker=STICKER_FILE_ID, thread_id=result.thread_id)
    _enqueue_outbox_text(
        chat_id=chat_id,
        text=f"С твоего счёта списано {EVENT_COST} сит за запуск ивента",
        thread_id=result.thread_id,
    )

    await message.answer(
        "Хочешь напоминание о старте? Нажми кнопку!",
        reply_markup=remind_keyboard(),
        **send_kwargs,
    )



def _schedule_event_flow(bot: Bot, chat_id: int) -> None:
    existing = _event_flow_tasks.get(chat_id)
    if existing and not existing.done():
        return
    task = asyncio.create_task(_run_event_flow(bot, chat_id))
    _event_flow_tasks[chat_id] = task


async def _run_event_flow(bot: Bot, chat_id: int):
    event = _store.get_event(chat_id)
    if not event:
        return
    thread_id = event["thread_id"]
    event_token = f"{chat_id}:{int(event['created_at'] or int(datetime.now().timestamp()))}"
    send_kwargs = _send_kwargs_from_thread_id(chat_id, thread_id)

    try:
        await asyncio.sleep(PREPARE_DELAY_SEC - 7 * 60)
        await bot.send_message(chat_id, "До групповой мастурбации осталось 7 минут!", **send_kwargs)

        await asyncio.sleep(3 * 60)
        await bot.send_message(chat_id, "До групповой мастурбации осталось 4 минуты!", **send_kwargs)

        await asyncio.sleep(3 * 60)
        await bot.send_message(chat_id, "До групповой мастурбации осталась 1 минута!", **send_kwargs)

        await asyncio.sleep(1 * 60)

        reminders = _store.list_reminders(chat_id)
        if reminders:
            mentions: list[str] = []
            with closing(get_connection()) as conn:
                cur = conn.cursor()
                for row in reminders:
                    uid = int(row["user_id"])
                    cur.execute("SELECT nick FROM users WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
                    nick_row = cur.fetchone()
                    nick = (nick_row["nick"] or "").strip() if nick_row else ""
                    mentions.append(nick or row["display_name"])
            text = " ".join(mentions) + " — скоро начинаем!!"
            await bot.send_message(chat_id, text, **send_kwargs)

        msg = await bot.send_message(
            chat_id,
            "Поехали! Для участия нажми на кнопку",
            reply_markup=join_keyboard(),
            **send_kwargs,
        )
        _store.set_join_window(
            chat_id=chat_id,
            is_open=True,
            status="joining",
            join_message_id=msg.message_id,
        )

        event = _store.get_event(chat_id)
        starter_user_id = int(event["started_by_user_id"]) if event else 0
        subscription_ping_text = build_subscription_ping_text(
            chat_id,
            exclude_user_id=starter_user_id,
            suffix="Окно участия открыто. Если хочешь в дело, жми кнопку.",
        )
        if subscription_ping_text:
            await bot.send_message(chat_id, subscription_ping_text, **send_kwargs)

        await asyncio.sleep(JOIN_WINDOW_SEC - 60)
        await bot.send_message(chat_id, "⏳ Осталась одна минута! Готовимся!", **send_kwargs)
        await asyncio.sleep(30)
        await bot.send_message(chat_id, "🎯 Целимся!!", **send_kwargs)
        await asyncio.sleep(20)
        await bot.send_message(chat_id, "🔟 10-секундная готовность!", **send_kwargs)
        await asyncio.sleep(9)
        await bot.send_message(chat_id, "💥 ПЛИ!", **send_kwargs)
        await asyncio.sleep(1)
    finally:
        _store.set_join_window(chat_id=chat_id, is_open=False, status="finishing")

    event = _store.get_event(chat_id)
    join_message_id = int(event["join_message_id"]) if event and event["join_message_id"] else None
    if join_message_id:
        _store.enqueue_outbox(
            chat_id=chat_id,
            kind="edit_reply_markup",
            payload={"message_id": join_message_id},
        )

    participant_rows = _store.list_participants(chat_id=chat_id, role="participant")
    spectator_rows = _store.list_participants(chat_id=chat_id, role="spectator")
    freebie_rows = _store.list_participants(chat_id=chat_id, is_freebie=True)
    participants = [int(row["user_id"]) for row in participant_rows]
    participant_names = {int(row["user_id"]): str(row["display_name"]) for row in participant_rows}
    spectators = [int(row["user_id"]) for row in spectator_rows]
    spectator_names = {int(row["user_id"]): str(row["display_name"]) for row in spectator_rows}
    freebies = [int(row["user_id"]) for row in freebie_rows]
    freebie_names = {int(row["user_id"]): str(row["display_name"]) for row in freebie_rows}
    lucky_dick_user_id: int | None = None
    lucky_dick_name: str | None = None
    winner_id: int | None = None
    winner_name: str | None = None
    winner_reward_sits = 0.0
    lucky_freebie_user_id: int | None = None
    lucky_freebie_name: str | None = None

    if not participants:
        await bot.send_message(chat_id, "Групповая мастурбация окончена! Никто не присоединился 😢", **send_kwargs)
    else:
        lines = [participant_names.get(uid) or get_user_display_name(uid, chat_id) for uid in participants]
        text = "Групповая мастурбация окончена! Спасибо всем участникам. Вот они сверху вниз:\n" + "\n".join(lines)
        await bot.send_message(chat_id, text, **send_kwargs)

        lucky_id = random.choice(participants)
        lucky_name = participant_names.get(lucky_id) or get_user_display_name(lucky_id, chat_id)
        lucky_mention = get_winner_mention(chat_id, lucky_id, lucky_name)
        lucky_dick_user_id = lucky_id
        lucky_dick_name = lucky_name
        update_dick_length(lucky_id, chat_id, 1)
        lucky_sex = get_user_sex(lucky_id, chat_id)
        verb = "мастурбировал" if lucky_sex != "f" else "мастурбировала"
        await bot.send_message(
            chat_id,
            f"🍆 {lucky_mention} так усердно {verb}, что член подрос на 1 см! Так держать!",
            **send_kwargs,
        )

        winner_id = random.choice(participants)
        winner_name = participant_names.get(winner_id) or get_user_display_name(winner_id, chat_id)
        winner_mention = get_winner_mention(chat_id, winner_id, winner_name)
        reward = len(participants) + 1
        winner_reward_sits = float(reward)
        add_sits(chat_id, winner_id, reward)
        await bot.send_message(chat_id, f"🎉 Победитель: {winner_mention}! Получает {reward} сит!", **send_kwargs)
        await update_quest_progress(winner_id, chat_id, "group_win", 1, bot=bot)
        try:
            log_masturbation_results(chat_id, participants, winner_id, reward)
        except Exception:
            logger.exception("[group] failed to log masturbation results")

        if freebies:
            lucky_freebie = random.choice(freebies)
            lucky_freebie_name = freebie_names.get(lucky_freebie) or get_user_display_name(lucky_freebie, chat_id)
            lucky_freebie_user_id = lucky_freebie
            lucky_freebie_name = lucky_freebie_name
            add_sits(chat_id, lucky_freebie, 1)
            await bot.send_message(chat_id, f"✨ Также немножко капнуло на {lucky_freebie_name} — +1 сит!", **send_kwargs)

    _store.save_event_result(
        chat_id=chat_id,
        event_token=event_token,
        winner_user_id=winner_id,
        winner_name=winner_name,
        winner_reward_sits=winner_reward_sits,
        lucky_user_id=lucky_freebie_user_id,
        lucky_name=lucky_freebie_name,
        lucky_dick_user_id=lucky_dick_user_id,
        lucky_dick_name=lucky_dick_name,
        participants=[
            {
                "user_id": int(row["user_id"]),
                "name": str(row["display_name"]),
                "role": "participant",
                "is_starter": bool(int(row["user_id"]) == int(starter_user_id)),
            }
            for row in participant_rows
        ],
        spectators=[
            {
                "user_id": int(row["user_id"]),
                "name": str(spectator_names.get(int(row["user_id"])) or row["display_name"]),
                "role": "spectator",
            }
            for row in spectator_rows
        ],
    )

    _store.finish_event(chat_id)
    _event_flow_tasks.pop(chat_id, None)
