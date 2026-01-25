import random
from datetime import date
from typing import Optional, Dict, Tuple, List

from aiogram import types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db import get_connection, get_user, add_or_update_user, get_user_display_name, get_user_sex, add_sits


class DickStates(StatesGroup):
    waiting_for_bet = State()


CHALLENGES: Dict[Tuple[int, int], Dict[str, int]] = {}


def ensure_dicks_table() -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dicks (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                length INTEGER DEFAULT 0,
                grow_date TEXT DEFAULT '',
                buff TEXT DEFAULT '',
                buff_exp TEXT DEFAULT '',
                PRIMARY KEY (user_id, chat_id)
            )
        """)
        conn.commit()


def get_or_create_dick(user_id: int, chat_id: int) -> dict:
    ensure_dicks_table()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM dicks WHERE user_id=? AND chat_id=?",
            (user_id, chat_id),
        )
        row = cur.fetchone()
        if row:
            return dict(row)
        cur.execute(
            """
            INSERT INTO dicks (user_id, chat_id, length, grow_date, buff, buff_exp)
            VALUES (?, ?, 0, '', '', '')
            """,
            (user_id, chat_id),
        )
        conn.commit()
        return {
            "user_id": user_id,
            "chat_id": chat_id,
            "length": 0,
            "grow_date": "",
            "buff": "",
            "buff_exp": "",
        }


def get_dick(user_id: int, chat_id: int) -> dict:
    return get_or_create_dick(user_id, chat_id)


def update_dick_length(user_id: int, chat_id: int, delta: int) -> int:
    dick = get_or_create_dick(user_id, chat_id)
    new_length = (dick["length"] or 0) + delta
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE dicks SET length=? WHERE user_id=? AND chat_id=?",
            (new_length, user_id, chat_id),
        )
        conn.commit()
    return new_length


def set_grow_date(user_id: int, chat_id: int, date_str: str) -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE dicks SET grow_date=? WHERE user_id=? AND chat_id=?",
            (date_str, user_id, chat_id),
        )
        conn.commit()


def get_dick_rankings(chat_id: int, only_grown: bool = False) -> List[dict]:
    ensure_dicks_table()
    with get_connection() as conn:
        cur = conn.cursor()
        if only_grown:
            cur.execute(
                """
                SELECT user_id, length, grow_date
                FROM dicks
                WHERE chat_id=? AND grow_date != ''
                ORDER BY length DESC, user_id ASC
                """,
                (chat_id,),
            )
        else:
            cur.execute(
                """
                SELECT user_id, length, grow_date
                FROM dicks
                WHERE chat_id=?
                ORDER BY length DESC, user_id ASC
                """,
                (chat_id,),
            )
        return [dict(row) for row in cur.fetchall()]


def get_user_place(chat_id: int, user_id: int) -> int:
    rankings = get_dick_rankings(chat_id)
    for idx, row in enumerate(rankings, start=1):
        if row["user_id"] == user_id:
            return idx
    return len(rankings) + 1


def get_growth_delta(current_length: int) -> int:
    shift = current_length // 50
    min_delta = -5 - shift
    max_delta = 15 - shift
    return random.randint(min_delta, max_delta)


def format_menu_text(
    user_id: int,
    chat_id: int,
    result_line: Optional[str] = None,
) -> str:
    dick = get_dick(user_id, chat_id)
    length = dick["length"] or 0
    place = get_user_place(chat_id, user_id)
    lines = [f"🍆 Твой член — {length} см, а твоё место в рейтинге — {place}"]

    today = date.today().isoformat()
    if result_line:
        lines.append(result_line)
    elif dick.get("grow_date") == today:
        lines.append("⏳ Завтра ты сможешь вырастить его снова")

    if dick.get("buff"):
        buff_exp = dick.get("buff_exp") or "?"
        lines.append(f"✨ У тебя есть бафф {dick['buff']}, который действует до {buff_exp}.")

    return "\n".join(lines)


def build_menu_keyboard(user_id: int, length: int, grew_today: bool) -> InlineKeyboardMarkup:
    buttons = []
    if not grew_today:
        buttons.append([InlineKeyboardButton(text="🌱 Растить", callback_data=f"dick:grow:{user_id}")])
    if length > 0:
        buttons.append([InlineKeyboardButton(text="⚔️ Бросить вызов", callback_data=f"dick:challenge:{user_id}")])
    buttons.append([
        InlineKeyboardButton(text="🛒 Донатшоп", callback_data=f"dick:shop:{user_id}"),
        InlineKeyboardButton(text="🏆 Рейтинг", callback_data=f"dick:rating:{user_id}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_shop_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"dick:shop:back:{user_id}"),
        ],
        [
            InlineKeyboardButton(text="🍆 5см за 10 сит", callback_data=f"dick:shop:buy:5:10:{user_id}"),
        ],
        [
            InlineKeyboardButton(text="🍆🍆 15см за 25 сит", callback_data=f"dick:shop:buy:15:25:{user_id}"),
        ],
        [
            InlineKeyboardButton(text="🍆🍆🍆 35см за 50 сит", callback_data=f"dick:shop:buy:35:50:{user_id}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_cancel_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"dick:cancel:{user_id}")]
        ]
    )


def build_rating_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📜 Показать полный рейтинг", callback_data="dick:rating_full")]
        ]
    )


def get_balance(chat_id: int, user_id: int) -> int:
    user = get_user(user_id, chat_id)
    if not user:
        return 0
    return user["sits"] or 0


def build_rating_text(
    chat_id: int,
    requester_id: Optional[int],
    full: bool = False,
) -> str:
    rankings = get_dick_rankings(chat_id, only_grown=True)
    if not rankings:
        return "🏆 Рейтинг пока пуст."

    limit = len(rankings) if full else 10
    lines = []
    for idx, row in enumerate(rankings[:limit], start=1):
        name = get_user_display_name(row["user_id"], chat_id)
        lines.append(f"{idx}. {name} — {row['length']} см")

    if not full and requester_id is not None:
        for idx, row in enumerate(rankings, start=1):
            if row["user_id"] == requester_id and idx > 10:
                name = get_user_display_name(row["user_id"], chat_id)
                lines.append("...")
                lines.append(f"{idx}. {name} — {row['length']} см")
                break

    return "🏆 Рейтинг длины членов:\n" + "\n".join(lines)


def get_gendered_word(sex: Optional[str], male: str, female: str) -> str:
    return female if sex == "f" else male


def calculate_win_chance(longer: int, shorter: int) -> float:
    if shorter <= 0:
        return 0.5
    ratio = longer / shorter
    chance = 0.5 - 0.05 * (ratio - 1)
    return max(0.45, min(0.5, chance))


def try_bite_dick(chat_id: int, victim_id: int) -> Optional[str]:
    dick = get_dick(victim_id, chat_id)
    length = dick["length"] or 0
    if length <= 0:
        return None
    if random.random() >= 0.1:
        return None
    bite_size = random.randint(1, 3)
    bite_size = min(bite_size, length)
    update_dick_length(victim_id, chat_id, -bite_size)
    victim_sex = get_user_sex(victim_id, chat_id)
    verb = get_gendered_word(victim_sex, "потерял", "потеряла")
    return f"🩸 {get_user_display_name(victim_id, chat_id)} {verb} {bite_size} см члена от укуса!"


def register_dick_handlers(dp):
    @dp.message(Command("dick"))
    async def dick_command(message: types.Message):
        chat_id = message.chat.id
        user_id = message.from_user.id
        name = message.from_user.full_name or str(user_id)
        if get_user(user_id, chat_id) is None:
            add_or_update_user(user_id, chat_id, name=name, sits=0, punished=0, sex=None)
        else:
            add_or_update_user(user_id, chat_id, name=name)

        dick = get_or_create_dick(user_id, chat_id)
        today = date.today().isoformat()
        grew_today = dick.get("grow_date") == today
        text = format_menu_text(user_id, chat_id)
        keyboard = build_menu_keyboard(user_id, dick["length"], grew_today)
        await message.answer(text, reply_markup=keyboard)

    @dp.callback_query(F.data.startswith("dick:") & ~F.data.startswith("dick:duel:"))
    async def dick_callback(query: types.CallbackQuery, state: FSMContext):
        parts = query.data.split(":")
        if len(parts) < 2:
            await query.answer()
            return

        action = parts[1]

        def is_owner_allowed(owner_id: int) -> bool:
            if query.from_user.id != owner_id:
                return False
            return True

        if action == "rating_full":
            chat_id = query.message.chat.id
            text = build_rating_text(chat_id, requester_id=None, full=True)
            await query.message.edit_text(text, reply_markup=None)
            await query.answer()
            return

        if len(parts) < 3:
            await query.answer()
            return

        owner_id = int(parts[-1])
        if not is_owner_allowed(owner_id):
            await query.answer(
                "Это меню другого пользователя. Вызови своё с помощью /dick",
                show_alert=True,
            )
            return

        chat_id = query.message.chat.id
        dick = get_or_create_dick(owner_id, chat_id)
        today = date.today().isoformat()

        if action == "grow":
            if dick.get("grow_date") == today:
                await query.answer("Сегодня ты уже растил.", show_alert=True)
                return

            current_length = dick["length"] or 0
            delta = get_growth_delta(current_length)
            new_length = update_dick_length(owner_id, chat_id, delta)
            set_grow_date(owner_id, chat_id, today)

            if delta > 0:
                result_line = f"📈 Твой гордый пинус вырос на {delta} см!"
            elif delta < 0:
                result_line = f"📉 Твоей бруньке завидовали остальные, поэтому она скромно скукожилась на {abs(delta)} сантиметров"
            else:
                result_line = "🪨 Член застыл в ожидании. Сегодня без прироста, попробуй завтра"

            text = format_menu_text(owner_id, chat_id, result_line=result_line)
            keyboard = build_menu_keyboard(owner_id, new_length, grew_today=True)
            await query.message.edit_text(text, reply_markup=keyboard)
            await query.answer()
            return

        if action == "challenge":
            if dick["length"] <= 0:
                await query.answer("Пока нечем бросать вызов 😢", show_alert=True)
                return

            await state.set_state(DickStates.waiting_for_bet)
            await state.update_data(
                chat_id=chat_id,
                menu_message_id=query.message.message_id,
                owner_id=owner_id,
            )
            prompt = f"⚔️ Введите вашу ставку (ваша длина — {dick['length']} см)"
            await query.message.edit_text(prompt, reply_markup=build_cancel_keyboard(owner_id))
            await query.answer()
            return

        if action == "cancel":
            data = await state.get_data()
            if data.get("owner_id") != owner_id:
                await query.answer()
                return
            await state.clear()
            dick = get_or_create_dick(owner_id, chat_id)
            grew_today = dick.get("grow_date") == today
            text = format_menu_text(owner_id, chat_id)
            keyboard = build_menu_keyboard(owner_id, dick["length"], grew_today)
            await query.message.edit_text(text, reply_markup=keyboard)
            await query.answer()
            return

        if action == "shop":
            if len(parts) == 3:
                balance = get_balance(chat_id, owner_id)
                text = f"🛒 Магазин за сит. Твой баланс — {balance}"
                await query.message.edit_text(text, reply_markup=build_shop_keyboard(owner_id))
                await query.answer()
                return

            subaction = parts[2]
            if subaction == "back":
                dick = get_or_create_dick(owner_id, chat_id)
                grew_today = dick.get("grow_date") == today
                text = format_menu_text(owner_id, chat_id)
                keyboard = build_menu_keyboard(owner_id, dick["length"], grew_today)
                await query.message.edit_text(text, reply_markup=keyboard)
                await query.answer()
                return

            if subaction == "buy":
                cm = int(parts[3])
                price = int(parts[4])
                balance = get_balance(chat_id, owner_id)
                if balance < price:
                    await query.answer("Недостаточно сит для покупки 😢", show_alert=True)
                    return
                add_sits(chat_id, owner_id, -price)
                new_length = update_dick_length(owner_id, chat_id, cm)
                await query.message.answer(
                    f"✅ Покупка успешна! Списано {price} сит, новая длина — {new_length} см."
                )
                await query.answer()
                return

        if action == "rating":
            text = build_rating_text(chat_id, requester_id=owner_id, full=False)
            await query.message.answer(text, reply_markup=build_rating_keyboard())
            await query.answer()
            return

        await query.answer()

    @dp.message(DickStates.waiting_for_bet)
    async def dick_bet_input(message: types.Message, state: FSMContext):
        data = await state.get_data()
        owner_id = data.get("owner_id")
        chat_id = data.get("chat_id")
        menu_message_id = data.get("menu_message_id")

        if message.from_user.id != owner_id or message.chat.id != chat_id:
            return

        dick = get_or_create_dick(owner_id, chat_id)
        length = dick["length"] or 0

        try:
            bet = int(message.text.strip())
        except (TypeError, ValueError):
            await message.answer("Ставка должна быть целым числом. Попробуй ещё раз.")
            return

        if bet <= 0:
            await message.answer("Ставка должна быть положительным числом. Попробуй ещё раз.")
            return
        if bet > length:
            await message.answer(f"Ставка не может быть больше длины. Твоя длина — {length} см.")
            return

        await state.clear()

        try:
            dick = get_or_create_dick(owner_id, chat_id)
            today = date.today().isoformat()
            grew_today = dick.get("grow_date") == today
            text = format_menu_text(owner_id, chat_id)
            keyboard = build_menu_keyboard(owner_id, dick["length"], grew_today)
            await message.bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=menu_message_id,
                reply_markup=keyboard,
            )
        except Exception:
            pass

        sex = get_user_sex(owner_id, chat_id)
        verb = get_gendered_word(sex, "бросил", "бросила")
        challenger_name = get_user_display_name(owner_id, chat_id)
        challenge_text = (
            f"⚔️ {challenger_name} {verb} вызов чату! "
            f"Размер ставки — {bet} см. Кто посмеет ответить?"
        )
        msg = await message.answer(
            challenge_text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🗡️ Сразиться на мечах", callback_data=f"dick:duel:{owner_id}:{bet}")]
                ]
            ),
        )
        CHALLENGES[(chat_id, msg.message_id)] = {
            "challenger_id": owner_id,
            "bet": bet,
        }

    @dp.callback_query(F.data.startswith("dick:duel:"))
    async def dick_duel_callback(query: types.CallbackQuery):
        chat_id = query.message.chat.id
        message_id = query.message.message_id

        challenge = CHALLENGES.get((chat_id, message_id))
        if not challenge:
            await query.answer("Этот вызов уже неактуален.", show_alert=True)
            return

        challenger_id = challenge["challenger_id"]
        bet = challenge["bet"]

        if query.from_user.id == challenger_id:
            await query.answer("Нельзя отвечать самому себе.", show_alert=True)
            return

        accepter_id = query.from_user.id
        accepter_dick = get_or_create_dick(accepter_id, chat_id)
        challenger_dick = get_or_create_dick(challenger_id, chat_id)

        if (challenger_dick["length"] or 0) < bet:
            await query.answer("Вызов устарел: длина инициатора уже меньше ставки.", show_alert=True)
            return

        if (accepter_dick["length"] or 0) < bet:
            await query.answer(
                f"Брунька не отросла отвечать на такие вызовы. "
                f"У тебя {accepter_dick['length']} см, а надо {bet} см.",
                show_alert=True,
            )
            return

        length_a = challenger_dick["length"] or 0
        length_b = accepter_dick["length"] or 0
        if length_a >= length_b:
            longer_id, longer_length = challenger_id, length_a
            shorter_id, shorter_length = accepter_id, length_b
        else:
            longer_id, longer_length = accepter_id, length_b
            shorter_id, shorter_length = challenger_id, length_a

        chance_longer = calculate_win_chance(longer_length, shorter_length)
        longer_wins = random.random() < chance_longer
        winner_id = longer_id if longer_wins else shorter_id
        loser_id = shorter_id if longer_wins else longer_id

        winner_length = update_dick_length(winner_id, chat_id, bet)
        loser_length = update_dick_length(loser_id, chat_id, -bet)

        winner_place = get_user_place(chat_id, winner_id)
        loser_place = get_user_place(chat_id, loser_id)

        winner_name = get_user_display_name(winner_id, chat_id)
        loser_name = get_user_display_name(loser_id, chat_id)

        winner_sex = get_user_sex(winner_id, chat_id)
        loser_sex = get_user_sex(loser_id, chat_id)

        verb_win = get_gendered_word(winner_sex, "победил", "победила")
        winner_pronoun = get_gendered_word(winner_sex, "Его", "Её")
        loser_word = get_gendered_word(loser_sex, "проигравшего", "проигравшей")

        result_text = (
            f"🏁 В битве {verb_win} {winner_name}! "
            f"{winner_pronoun} длина теперь {winner_length} см, а место в рейтинге — {winner_place}\n\n"
            f"У {loser_word} {loser_name} теперь {loser_length} см и {loser_place} место"
        )

        await query.message.edit_text(result_text, reply_markup=None)
        await query.answer()
        CHALLENGES.pop((chat_id, message_id), None)


ensure_dicks_table()
