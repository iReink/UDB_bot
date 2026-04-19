import asyncio
import html
import logging
import math
import random
import time
from dataclasses import dataclass, field

from aiogram import Bot, Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.command import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from db import add_sits, get_user, get_user_display_name
from sits import normalize_sits

INITIAL_HEALTH = 100
MIN_DAMAGE = 16
MAX_DAMAGE = 24
CRIT_CHANCE = 5
BET_COST = 5
WIN_SITS_MIN = 2
WIN_SITS_MAX = 4
CHALLENGE_TIMEOUT_MINUTES = 10

ROUND_TIMEOUT_SECONDS = 72
ROUND_UPDATE_SECONDS = 12
DISPLAY_SECONDS_STEP = 10

TARGET_ORDER = ("head", "body", "legs")
TARGET_LABELS = {
    "head": "голова",
    "body": "тело",
    "legs": "ноги",
}

_fight_sequence = 0


@dataclass
class FightChoice:
    attack: str | None = None
    defense: str | None = None


@dataclass
class PendingChallenge:
    challenge_id: int
    chat_id: int
    challenger_id: int
    challenger_name: str
    message_id: int
    thread_id: int | None
    challenger_balance_after_bet: int | float
    timeout_task: asyncio.Task | None = None


@dataclass
class FightSession:
    fight_id: int
    chat_id: int
    message_id: int
    thread_id: int | None
    player1_id: int
    player1_name: str
    player2_id: int
    player2_name: str
    player1_health: int = INITIAL_HEALTH
    player2_health: int = INITIAL_HEALTH
    round_number: int = 1
    round_started_at: float = 0.0
    round_deadline: float = 0.0
    last_round_lines: tuple[str, str] = ("— ждёт сигнала к атаке", "— ждёт сигнала к атаке")
    choices: dict[int, FightChoice] = field(default_factory=dict)
    round_task: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    render_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    finished: bool = False

    def player_name(self, user_id: int) -> str:
        return self.player1_name if user_id == self.player1_id else self.player2_name


ACTIVE_CHALLENGES: dict[int, PendingChallenge] = {}
ACTIVE_FIGHTS: dict[int, FightSession] = {}


async def get_current_sits(user_id: int, chat_id: int) -> int | float:
    user = get_user(user_id, chat_id)
    return normalize_sits(user["sits"]) if user and user["sits"] is not None else 0


def next_fight_id() -> int:
    global _fight_sequence
    _fight_sequence += 1
    return _fight_sequence


def build_challenge_keyboard(challenge_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=f"⚔️ Принять вызов ({BET_COST} сита)",
            callback_data=f"fight_accept:{challenge_id}",
        )
    )
    return kb.as_markup()


def build_fight_keyboard(fight: FightSession) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for action, icon in (("attack", "🗡"), ("defense", "🛡")):
        for target in TARGET_ORDER:
            kb.row(
                InlineKeyboardButton(
                    text=f"{icon} {'Атака' if action == 'attack' else 'Защита'}: {TARGET_LABELS[target].capitalize()}",
                    callback_data=f"fight_action:{fight.fight_id}:{fight.round_number}:{action}:{target}",
                )
            )
    return kb.as_markup()


def get_target_name(target_key: str) -> str:
    return TARGET_LABELS.get(target_key, target_key)


def get_health_percent(health: int) -> int:
    return max(0, min(100, health))


def build_health_bar(health: int) -> str:
    percent = get_health_percent(health)
    filled = math.ceil(percent / 10) if percent > 0 else 0
    empty = 10 - filled
    return ("❤️" * filled) + ("🤍" * empty)


def get_display_seconds(fight: FightSession) -> int:
    remaining = max(0.0, fight.round_deadline - time.monotonic())
    if remaining <= 0:
        return 0
    return math.ceil(remaining / ROUND_UPDATE_SECONDS) * DISPLAY_SECONDS_STEP


def round_expired(fight: FightSession) -> bool:
    return time.monotonic() >= fight.round_deadline


def format_player_status(name: str, choice: FightChoice) -> str:
    safe_name = html.escape(name)
    attack_mark = "✅" if choice.attack else "❌"
    defense_mark = "✅" if choice.defense else "❌"
    return f"{safe_name} — атака {attack_mark} защита {defense_mark}"


def build_fight_text(fight: FightSession) -> str:
    p1_percent = get_health_percent(fight.player1_health)
    p2_percent = get_health_percent(fight.player2_health)
    p1_choice = fight.choices[fight.player1_id]
    p2_choice = fight.choices[fight.player2_id]

    if fight.finished:
        round_line = f"Раунд №{fight.round_number}. Бой завершён"
    elif round_expired(fight):
        round_line = f"Раунд №{fight.round_number}. Время вышло, фиксируем выбор"
    else:
        round_line = (
            f"Раунд №{fight.round_number}. "
            f"Сделайте выбор в течение {get_display_seconds(fight)} секунд"
        )

    return (
        f"⚔️ <b>{html.escape(fight.player1_name)}</b> vs <b>{html.escape(fight.player2_name)}</b>\n\n"
        f"{html.escape(fight.player1_name)} — {max(0, fight.player1_health)}/{INITIAL_HEALTH} ({p1_percent}%) {build_health_bar(fight.player1_health)}\n"
        f"{html.escape(fight.player2_name)} — {max(0, fight.player2_health)}/{INITIAL_HEALTH} ({p2_percent}%) {build_health_bar(fight.player2_health)}\n\n"
        f"{round_line}\n"
        f"{format_player_status(fight.player1_name, p1_choice)}\n"
        f"{format_player_status(fight.player2_name, p2_choice)}\n\n"
        f"Последний раунд:\n"
        f"{fight.last_round_lines[0]}\n"
        f"{fight.last_round_lines[1]}"
    )


async def edit_fight_message(bot: Bot, fight: FightSession) -> None:
    async with fight.render_lock:
        try:
            await bot.edit_message_text(
                chat_id=fight.chat_id,
                message_id=fight.message_id,
                text=build_fight_text(fight),
                reply_markup=None if fight.finished else build_fight_keyboard(fight),
                parse_mode="HTML",
            )
        except Exception:
            pass


async def safe_callback_answer(
    callback: types.CallbackQuery,
    text: str | None = None,
    *,
    show_alert: bool = False,
) -> None:
    try:
        await callback.answer(text, show_alert=show_alert)
    except TelegramBadRequest as exc:
        message = str(exc).lower()
        if "query is too old" in message or "query id is invalid" in message:
            logging.warning("[fight] callback answer expired for user %s", callback.from_user.id)
            return
        raise
    except Exception as exc:
        logging.warning("[fight] callback answer failed: %s", exc)


def calculate_attack_line(
    attacker_name: str,
    defender_name: str,
    attack_target: str,
    defense_target: str,
) -> tuple[int, str]:
    base_damage = random.randint(MIN_DAMAGE, MAX_DAMAGE)
    is_crit = random.randint(1, 100) <= CRIT_CHANCE
    damage = base_damage * 2 if is_crit else base_damage
    blocked = attack_target == defense_target
    if blocked:
        damage //= 2

    extras: list[str] = []
    if blocked:
        extras.append("блок")
    if is_crit:
        extras.append("крит")
    extras_text = f", {', '.join(extras)}" if extras else ""

    line = (
        f"{html.escape(attacker_name)}: удар в {get_target_name(attack_target)}, "
        f"защита {html.escape(defender_name)} — {get_target_name(defense_target)}, "
        f"урон {damage}{extras_text}"
    )
    return damage, line


def fight_is_ready(fight: FightSession) -> bool:
    return all(
        choice.attack and choice.defense
        for choice in fight.choices.values()
    )


def ensure_round_choices(fight: FightSession) -> None:
    for player_id in (fight.player1_id, fight.player2_id):
        choice = fight.choices[player_id]
        if not choice.attack:
            choice.attack = random.choice(TARGET_ORDER)
        if not choice.defense:
            choice.defense = random.choice(TARGET_ORDER)


async def finalize_fight(bot: Bot, fight: FightSession, winner_id: int, winner_reason: str) -> None:
    fight.finished = True
    if fight.round_task:
        fight.round_task.cancel()
        fight.round_task = None
    ACTIVE_FIGHTS.pop(fight.chat_id, None)

    await edit_fight_message(bot, fight)

    reward_bonus = random.randint(WIN_SITS_MIN, WIN_SITS_MAX)
    reward_total = BET_COST + reward_bonus
    add_sits(fight.chat_id, winner_id, reward_total)
    winner_name = get_user_display_name(winner_id, fight.chat_id)
    current_balance = await get_current_sits(winner_id, fight.chat_id)

    await bot.send_message(
        fight.chat_id,
        (
            f"{winner_reason}\n\n"
            f"🏆 Победитель: <b>{html.escape(winner_name)}</b>\n"
            f"💰 Награда: {reward_total} сита "
            f"(возврат ставки {BET_COST} + выигрыш {reward_bonus}).\n"
            f"Текущий баланс: {current_balance} сита."
        ),
        parse_mode="HTML",
        message_thread_id=fight.thread_id,
    )


async def resolve_round(bot: Bot, fight: FightSession) -> None:
    next_round_needed = False
    winner_id: int | None = None
    winner_reason = ""

    async with fight.lock:
        if fight.finished:
            return

        ensure_round_choices(fight)

        p1_choice = fight.choices[fight.player1_id]
        p2_choice = fight.choices[fight.player2_id]

        damage_to_p2, line1 = calculate_attack_line(
            fight.player1_name,
            fight.player2_name,
            p1_choice.attack,
            p2_choice.defense,
        )
        damage_to_p1, line2 = calculate_attack_line(
            fight.player2_name,
            fight.player1_name,
            p2_choice.attack,
            p1_choice.defense,
        )

        fight.player1_health -= damage_to_p1
        fight.player2_health -= damage_to_p2
        fight.last_round_lines = (line1, line2)

        if fight.player1_health <= 0 and fight.player2_health <= 0:
            winner_id = fight.player1_id
            winner_reason = (
                "💥 Оба бойца рухнули одновременно. "
                "По правилам клуба победа уходит тому, кто первым бросил вызов."
            )
        elif fight.player1_health <= 0:
            winner_id = fight.player2_id
            winner_reason = f"💥 <b>{html.escape(fight.player1_name)}</b> не выдержал натиска."
        elif fight.player2_health <= 0:
            winner_id = fight.player1_id
            winner_reason = f"💥 <b>{html.escape(fight.player2_name)}</b> повержен."
        else:
            fight.round_number += 1
            fight.round_started_at = time.monotonic()
            fight.round_deadline = fight.round_started_at + ROUND_TIMEOUT_SECONDS
            fight.choices = {
                fight.player1_id: FightChoice(),
                fight.player2_id: FightChoice(),
            }
            next_round_needed = True

    if winner_id is not None:
        await finalize_fight(bot, fight, winner_id, winner_reason)
        return

    await edit_fight_message(bot, fight)

    if next_round_needed:
        fight.round_task = asyncio.create_task(
            run_round_timer(bot, fight.chat_id, fight.fight_id, fight.round_number)
        )


async def run_round_timer(bot: Bot, chat_id: int, fight_id: int, round_number: int) -> None:
    try:
        for _ in range(ROUND_TIMEOUT_SECONDS // ROUND_UPDATE_SECONDS - 1):
            await asyncio.sleep(ROUND_UPDATE_SECONDS)

            fight = ACTIVE_FIGHTS.get(chat_id)
            if not fight or fight.fight_id != fight_id:
                return

            async with fight.lock:
                if fight.finished or fight.round_number != round_number:
                    return
                if fight_is_ready(fight):
                    return

            await edit_fight_message(bot, fight)

        await asyncio.sleep(ROUND_UPDATE_SECONDS)

        fight = ACTIVE_FIGHTS.get(chat_id)
        if not fight or fight.fight_id != fight_id:
            return

        async with fight.lock:
            if fight.finished or fight.round_number != round_number:
                return
            if fight.round_task and fight.round_task is asyncio.current_task():
                fight.round_task = None

        await resolve_round(bot, fight)
    except asyncio.CancelledError:
        return


async def challenge_timeout_check(bot: Bot, chat_id: int, challenge_id: int) -> None:
    try:
        await asyncio.sleep(CHALLENGE_TIMEOUT_MINUTES * 60)
        challenge = ACTIVE_CHALLENGES.get(chat_id)
        if not challenge or challenge.challenge_id != challenge_id:
            return

        add_sits(chat_id, challenge.challenger_id, BET_COST)
        ACTIVE_CHALLENGES.pop(chat_id, None)

        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=challenge.message_id,
            text=(
                f"<b>{html.escape(challenge.challenger_name)}</b> бросал вызов, "
                f"но никто не решился принять бой.\n\n"
                f"Ставка в {BET_COST} сита возвращена.\n"
                f"Текущий баланс: {challenge.challenger_balance_after_bet + BET_COST} сита."
            ),
            parse_mode="HTML",
        )
    except asyncio.CancelledError:
        return
    except Exception:
        ACTIVE_CHALLENGES.pop(chat_id, None)


async def start_fight(bot: Bot, challenge: PendingChallenge, accepter_id: int, accepter_name: str) -> None:
    if challenge.timeout_task:
        challenge.timeout_task.cancel()
    ACTIVE_CHALLENGES.pop(challenge.chat_id, None)

    fight = FightSession(
        fight_id=challenge.challenge_id,
        chat_id=challenge.chat_id,
        message_id=challenge.message_id,
        thread_id=challenge.thread_id,
        player1_id=challenge.challenger_id,
        player1_name=challenge.challenger_name,
        player2_id=accepter_id,
        player2_name=accepter_name,
    )
    fight.choices = {
        fight.player1_id: FightChoice(),
        fight.player2_id: FightChoice(),
    }
    fight.round_started_at = time.monotonic()
    fight.round_deadline = fight.round_started_at + ROUND_TIMEOUT_SECONDS
    ACTIVE_FIGHTS[fight.chat_id] = fight

    await edit_fight_message(bot, fight)

    fight.round_task = asyncio.create_task(run_round_timer(bot, fight.chat_id, fight.fight_id, fight.round_number))

    challenger_user = get_user(challenge.challenger_id, challenge.chat_id)
    challenger_nick = (challenger_user["nick"] or "").strip() if challenger_user and "nick" in challenger_user.keys() else ""
    challenger_ping = challenger_nick or html.escape(challenge.challenger_name)
    await bot.send_message(
        challenge.chat_id,
        f"{challenger_ping}, твой вызов принял <b>{html.escape(accepter_name)}</b>! Бой начался.",
        parse_mode="HTML",
        message_thread_id=challenge.thread_id,
    )


def register_fight_club_handlers(dp: Dispatcher):
    @dp.message(Command("fight"))
    async def fight_menu(message: types.Message):
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="👊 Бросить вызов (5 сит)", callback_data="fight_challenge"))
        await message.answer(
            "Добро пожаловать в Бойцовский клуб! Готов испытать свою силу и удачу?",
            reply_markup=kb.as_markup(),
        )

    @dp.callback_query(F.data == "fight_challenge")
    async def process_fight_challenge(callback: types.CallbackQuery):
        challenger_id = callback.from_user.id
        chat_id = callback.message.chat.id

        if chat_id in ACTIVE_CHALLENGES or chat_id in ACTIVE_FIGHTS:
            await safe_callback_answer(callback, "В этом чате уже есть активный вызов или бой.", show_alert=True)
            return

        current_sits = await get_current_sits(challenger_id, chat_id)
        if current_sits < BET_COST:
            await safe_callback_answer(
                callback,
                f"Недостаточно сита для вызова. Нужно {BET_COST} сита.",
                show_alert=True,
            )
            return

        challenger_name = get_user_display_name(challenger_id, chat_id)
        add_sits(chat_id, challenger_id, -BET_COST)

        challenge_id = next_fight_id()
        await callback.message.edit_text(
            (
                f"<b>{html.escape(challenger_name)}</b> бросил вызов в Бойцовский клуб!\n"
                f"Ставка на вход: {BET_COST} сита.\n"
                f"Вызов активен {CHALLENGE_TIMEOUT_MINUTES} минут."
            ),
            reply_markup=build_challenge_keyboard(challenge_id),
            parse_mode="HTML",
        )

        challenge = PendingChallenge(
            challenge_id=challenge_id,
            chat_id=chat_id,
            challenger_id=challenger_id,
            challenger_name=challenger_name,
            message_id=callback.message.message_id,
            thread_id=callback.message.message_thread_id,
            challenger_balance_after_bet=current_sits - BET_COST,
        )
        challenge.timeout_task = asyncio.create_task(challenge_timeout_check(callback.bot, chat_id, challenge_id))
        ACTIVE_CHALLENGES[chat_id] = challenge

        await safe_callback_answer(callback, "Вызов брошен. Ждём соперника.")

    @dp.callback_query(F.data.startswith("fight_accept:"))
    async def process_accept_challenge(callback: types.CallbackQuery):
        chat_id = callback.message.chat.id
        accepter_id = callback.from_user.id

        challenge = ACTIVE_CHALLENGES.get(chat_id)
        if not challenge:
            await safe_callback_answer(callback, "Этот вызов уже неактивен.", show_alert=True)
            return

        if accepter_id == challenge.challenger_id:
            await safe_callback_answer(callback, "Это твой собственный вызов. Жди соперника.", show_alert=True)
            return

        _, challenge_id_text = callback.data.split(":")
        if challenge.challenge_id != int(challenge_id_text) or challenge.message_id != callback.message.message_id:
            await safe_callback_answer(callback, "Этот вызов уже устарел.", show_alert=True)
            return

        current_sits = await get_current_sits(accepter_id, chat_id)
        if current_sits < BET_COST:
            await safe_callback_answer(
                callback,
                f"Недостаточно сита для принятия вызова. Нужно {BET_COST} сита.",
                show_alert=True,
            )
            return

        add_sits(chat_id, accepter_id, -BET_COST)
        accepter_name = get_user_display_name(accepter_id, chat_id)
        await safe_callback_answer(callback, "Вызов принят. Бой начался.")
        await start_fight(callback.bot, challenge, accepter_id, accepter_name)

    @dp.callback_query(F.data.startswith("fight_action:"))
    async def process_fight_action(callback: types.CallbackQuery):
        chat_id = callback.message.chat.id
        fight = ACTIVE_FIGHTS.get(chat_id)
        if not fight:
            await safe_callback_answer(callback, "Этот бой уже завершён.", show_alert=True)
            return

        parts = callback.data.split(":")
        if len(parts) != 5:
            await safe_callback_answer(callback, "Некорректное действие.", show_alert=True)
            return

        _, fight_id_text, round_text, action, target = parts
        if fight.fight_id != int(fight_id_text):
            await safe_callback_answer(callback, "Это действие относится к другому бою.", show_alert=True)
            return

        user_id = callback.from_user.id
        if user_id not in (fight.player1_id, fight.player2_id):
            await safe_callback_answer(callback, "Только участники боя могут нажимать эти кнопки.", show_alert=True)
            return

        round_number = int(round_text)
        should_render = False
        should_resolve = False
        timer_task: asyncio.Task | None = None
        answer_text: str | None = None
        answer_alert = False

        async with fight.lock:
            if fight.finished:
                answer_text = "Этот бой уже завершён."
                answer_alert = True
            elif round_number != fight.round_number:
                answer_text = "Раунд уже обновился. Нажми кнопку ещё раз."
                answer_alert = True
            else:
                choice = fight.choices[user_id]
                current_value = getattr(choice, action, None)
                if current_value is not None:
                    label = "Атака" if action == "attack" else "Защита"
                    answer_text = f"{label} в этом раунде уже выбрана."
                    answer_alert = True
                else:
                    setattr(choice, action, target)
                    should_render = True
                    label = "атаку" if action == "attack" else "защиту"
                    answer_text = f"Выбор на {label} принят."

                    if fight_is_ready(fight):
                        should_resolve = True
                        should_render = False
                        timer_task = fight.round_task
                        fight.round_task = None

        if answer_text is not None:
            await safe_callback_answer(callback, answer_text, show_alert=answer_alert)

        if answer_alert:
            return

        if timer_task:
            timer_task.cancel()

        if should_render:
            await edit_fight_message(callback.bot, fight)

        if should_resolve:
            await resolve_round(callback.bot, fight)
            return

        if round_expired(fight):
            return
