import sqlite3
import asyncio
import random
from datetime import datetime

from aiogram import Bot, types, F, Dispatcher
from aiogram.filters.command import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from db import get_user, add_sits, get_user_display_name

# --- КОНСТАНТЫ ИГРЫ ---
INITIAL_HEALTH = 100
MIN_DAMAGE = 16
MAX_DAMAGE = 24
CRIT_CHANCE = 5
BET_COST = 10
WIN_SITS_MIN = 2
WIN_SITS_MAX = 8
CHALLENGE_TIMEOUT_MINUTES = 10

# --- ФРАЗЫ ---
ATTACK_PHRASES = [
    "{attacker_name} бьёт ногой с разворота в {target} {defender_name}!",
    "{attacker_name} со всей дури колошматит по {target} {defender_name}!",
    "{attacker_name} деликатно тыкает в {target} {defender_name}!",
    "{attacker_name} наносит сокрушительный удар в {target} {defender_name}!",
    "{attacker_name} мастерски пробивает {defender_name} в {target}!",
    "{attacker_name} финтом отправляет кулак в {target} {defender_name}!"
]

DEFENSE_PHRASES = [
    "{defender_name} прикрывает {target}.",
    "{defender_name} не даёт в обиду {target}.",
    "{defender_name} бережёт {target} смолоду.",
    "{defender_name} успевает поставить блок на {target}.",
    "{defender_name} уклоняется от удара в {target}.",
    "{defender_name} принимает удар в {target} на защиту!"
]

# --- FSM СОСТОЯНИЯ ---
class FightClubStates(StatesGroup):
    waiting_for_challenge_acceptance = State()
    choosing_attack = State()
    choosing_defense = State()

# --- УТИЛИТЫ ---
async def get_current_sits(user_id: int, chat_id: int) -> int:
    user = get_user(user_id, chat_id)
    return user["sits"] if user and user["sits"] else 0

def get_target_name(target_key: str) -> str:
    mapping = {
        "head": "голову",
        "body": "тело",
        "legs": "ноги"
    }
    return mapping.get(target_key, target_key)

# --- РЕГИСТРАЦИЯ ХЕНДЛЕРОВ ---
def register_fight_club_handlers(dp: Dispatcher):

    # Главное меню
    @dp.message(Command("fight"))
    async def fight_menu(message: types.Message, state: FSMContext):
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="👊 Бросить вызов (10 сита)", callback_data="fight_challenge"))
        await message.answer(
            "Добро пожаловать в Бойцовский клуб! Готов испытать свою силу и удачу?",
            reply_markup=kb.as_markup()
        )

    # Бросок вызова
    @dp.callback_query(F.data == "fight_challenge")
    async def process_fight_challenge(callback: types.CallbackQuery, state: FSMContext):
        challenger_id = callback.from_user.id
        chat_id = callback.message.chat.id
        challenger_name = get_user_display_name(challenger_id, chat_id)
        current_sits = await get_current_sits(challenger_id, chat_id)

        if current_sits < BET_COST:
            await callback.answer(f"Недостаточно сита! Нужно {BET_COST}.", show_alert=True)
            return

        add_sits(chat_id, challenger_id, -BET_COST)

        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(
            text=f"⚔️ Принять вызов ({BET_COST} сита)",
            callback_data=f"fight_accept_challenge:{challenger_id}"
        ))

        sent_message = await callback.message.answer(
            f"<b>{challenger_name}</b> бросил вызов! Стоимость: {BET_COST} сита.\n"
            f"Вызов активен {CHALLENGE_TIMEOUT_MINUTES} минут.",
            parse_mode="HTML",
            reply_markup=kb.as_markup()
        )

        await state.set_state(FightClubStates.waiting_for_challenge_acceptance)
        await state.update_data(
            challenger_id=challenger_id,
            challenger_name=challenger_name,
            chat_id=chat_id,
            challenge_message_id=sent_message.message_id,
            challenger_sits_at_challenge=current_sits - BET_COST,
            is_challenge_accepted=False
        )

        await callback.answer("Вызов брошен!", show_alert=False)
        asyncio.create_task(challenge_timeout_check(callback.bot, challenger_id, chat_id, sent_message.message_id, challenger_name, state))

    async def challenge_timeout_check(bot: Bot, challenger_id: int, chat_id: int, message_id: int, challenger_name: str, state: FSMContext):
        await asyncio.sleep(CHALLENGE_TIMEOUT_MINUTES * 60)
        data = await state.get_data()
        if data.get("is_challenge_accepted", False):
            return
        if data.get("challenger_id") == challenger_id and data.get("challenge_message_id") == message_id:
            add_sits(chat_id, challenger_id, BET_COST)
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"<b>{challenger_name}</b> бросал вызов, но никто не принял. Ставка возвращена.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            await state.clear()

    # Принятие вызова
    @dp.callback_query(F.data.startswith("fight_accept_challenge:"))
    async def process_accept_challenge(callback: types.CallbackQuery, state: FSMContext):
        accepter_id = callback.from_user.id
        chat_id = callback.message.chat.id
        _, challenger_id_str = callback.data.split(":")
        challenger_id = int(challenger_id_str)

        if accepter_id == challenger_id:
            await callback.answer("Нельзя принимать свой вызов!", show_alert=True)
            return

        # FSM Challenger
        challenger_state = FSMContext(
            storage=state.storage,
            key=StorageKey(bot_id=callback.bot.id, chat_id=chat_id, user_id=challenger_id)
        )
        challenger_data = await challenger_state.get_data()

        if challenger_data.get("is_challenge_accepted", False):
            await callback.answer("Этот вызов уже принят.", show_alert=True)
            return

        accepter_name = get_user_display_name(accepter_id, chat_id)
        current_sits = await get_current_sits(accepter_id, chat_id)
        if current_sits < BET_COST:
            await callback.answer(f"Недостаточно сита! Нужно {BET_COST}.", show_alert=True)
            return

        add_sits(chat_id, accepter_id, -BET_COST)

        challenger_name = challenger_data["challenger_name"]

        try:
            await callback.message.edit_text(
                f"<b>{challenger_name}</b> бросил вызов!\n"
                f"<b>{accepter_name}</b> принял его! 🤩 Бой начинается!",
                parse_mode="HTML"
            )
        except Exception:
            pass

        # Инициализация боя
        fight_data = {
            "chat_id": chat_id,
            "player1_id": challenger_id,
            "player2_id": accepter_id,
            "player1_name": challenger_name,
            "player2_name": accepter_name,
            "player1_health": INITIAL_HEALTH,
            "player2_health": INITIAL_HEALTH,
            "current_round": 1,
            "player1_action": {},
            "player2_action": {}
        }

        await challenger_state.update_data(fight=fight_data, is_challenge_accepted=True)
        await challenger_state.set_state(FightClubStates.choosing_attack)

        await state.update_data(fight=fight_data)
        await state.set_state(FightClubStates.choosing_attack)

        # Отправляем оба запроса одному сообщению с горизонтальными кнопками
        await ask_choices(callback.bot, fight_data, challenger_id)
        await ask_choices(callback.bot, fight_data, accepter_id)

        await callback.answer("Бой начинается!", show_alert=False)

# --- Функции для выбора атаки и защиты ---
async def ask_choices(bot: Bot, fight_data: dict, user_id: int):
    chat_id = fight_data["chat_id"]
    user_name = fight_data["player1_name"] if user_id == fight_data["player1_id"] else fight_data["player2_name"]

    kb_attack = InlineKeyboardBuilder()
    kb_attack.row(
        InlineKeyboardButton(text="🎯 Голова", callback_data=f"fight_attack:head:{user_id}"),
        InlineKeyboardButton(text="💪 Тело", callback_data=f"fight_attack:body:{user_id}"),
        InlineKeyboardButton(text="🦵 Ноги", callback_data=f"fight_attack:legs:{user_id}")
    )

    kb_defense = InlineKeyboardBuilder()
    kb_defense.row(
        InlineKeyboardButton(text="🛡️ Голова", callback_data=f"fight_defense:head:{user_id}"),
        InlineKeyboardButton(text="🛡️ Тело", callback_data=f"fight_defense:body:{user_id}"),
        InlineKeyboardButton(text="🛡️ Ноги", callback_data=f"fight_defense:legs:{user_id}")
    )

    try:
        await bot.send_message(chat_id, f"Раунд {fight_data['current_round']}. {user_name}, выберите атаку:", reply_markup=kb_attack.as_markup())
        await bot.send_message(chat_id, f"{user_name}, выберите защиту:", reply_markup=kb_defense.as_markup())
    except Exception:
        pass

# --- Обработка выбора атаки ---
async def process_attack_choice(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    target = parts[1]
    user_id = int(parts[2])

    data = await state.get_data()
    fight = data.get("fight", {})

    if not fight or user_id not in [fight["player1_id"], fight["player2_id"]]:
        await callback.answer("Это не ваш ход!", show_alert=True)
        return

    key = "player1_action" if user_id == fight["player1_id"] else "player2_action"
    fight.setdefault(key, {})
    fight[key]["attack"] = target
    await state.update_data(fight=fight)

    await callback.answer(f"Атака выбрана: {get_target_name(target)}", show_alert=False)
    try:
        await callback.message.edit_text(f"{get_user_display_name(user_id, fight['chat_id'])} выбрал цель для атаки.", reply_markup=None)
    except Exception:
        pass

    # Проверка готовности обоих игроков
    await try_calculate_round(callback.bot, fight, state)

# --- Обработка выбора защиты ---
async def process_defense_choice(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    target = parts[1]
    user_id = int(parts[2])

    data = await state.get_data()
    fight = data.get("fight", {})

    if not fight or user_id not in [fight["player1_id"], fight["player2_id"]]:
        await callback.answer("Это не ваш ход!", show_alert=True)
        return

    key = "player1_action" if user_id == fight["player1_id"] else "player2_action"
    fight.setdefault(key, {})
    fight[key]["defense"] = target
    await state.update_data(fight=fight)

    await callback.answer(f"Защита выбрана: {get_target_name(target)}", show_alert=False)
    try:
        await callback.message.edit_text(f"{get_user_display_name(user_id, fight['chat_id'])} выбрал что защищать.", reply_markup=None)
    except Exception:
        pass

    await try_calculate_round(callback.bot, fight, state)

# --- Проверка и расчет раунда ---
async def try_calculate_round(bot: Bot, fight: dict, state: FSMContext):
    p1_action = fight.get("player1_action", {})
    p2_action = fight.get("player2_action", {})

    if "attack" in p1_action and "defense" in p1_action and "attack" in p2_action and "defense" in p2_action:
        await calculate_round_results(bot, fight, state)

# --- Расчет результатов ---
async def calculate_round_results(bot: Bot, fight: dict, state: FSMContext):
    p1_id = fight["player1_id"]
    p2_id = fight["player2_id"]
    p1_name = fight["player1_name"]
    p2_name = fight["player2_name"]
    p1_health = fight["player1_health"]
    p2_health = fight["player2_health"]

    damage_to_p2, p1_desc = calculate_damage(p1_name, p2_name, fight["player1_action"]["attack"], fight["player2_action"]["defense"])
    damage_to_p1, p2_desc = calculate_damage(p2_name, p1_name, fight["player2_action"]["attack"], fight["player1_action"]["defense"])

    p1_health -= damage_to_p1
    p2_health -= damage_to_p2

    fight["player1_health"] = p1_health
    fight["player2_health"] = p2_health

    fight_summary = (
        f"<b>--- Раунд {fight['current_round']} ---</b>\n\n"
        f"{p1_desc}\n{p2_desc}\n\n"
        f"{p1_name}: {max(0,p1_health)} ❤️\n{p2_name}: {max(0,p2_health)} ❤️"
    )
    try:
        await bot.send_message(fight["chat_id"], fight_summary, parse_mode="HTML")
    except Exception:
        pass

    # Проверка победителя
    winner_id = None
    loser_id = None
    if p1_health <= 0 and p2_health <= 0:
        winner_id, loser_id = p1_id, p2_id
        text = f"Оба пали! Победитель: {p1_name}"
    elif p1_health <= 0:
        winner_id, loser_id = p2_id, p1_id
        text = f"{p1_name} повержен! Победитель: {p2_name}"
    elif p2_health <= 0:
        winner_id, loser_id = p1_id, p2_id
        text = f"{p2_name} повержен! Победитель: {p1_name}"
    else:
        winner_id = None

    if winner_id:
        win_sits = random.randint(WIN_SITS_MIN, WIN_SITS_MAX)
        add_sits(fight["chat_id"], winner_id, BET_COST + win_sits)
        text += f"\n💰 {get_user_display_name(winner_id, fight['chat_id'])} получает {BET_COST+win_sits} сита!"

        try:
            await bot.send_message(fight["chat_id"], text, parse_mode="HTML")
        except Exception:
            pass
        await state.clear()
        return

    # Продолжаем бой
    fight["current_round"] += 1
    fight["player1_action"] = {}
    fight["player2_action"] = {}
    await state.update_data(fight=fight)
    await ask_choices(bot, fight, p1_id)
    await ask_choices(bot, fight, p2_id)

# --- Расчет урона ---
def calculate_damage(attacker_name, defender_name, attack_target, defense_target):
    base = random.randint(MIN_DAMAGE, MAX_DAMAGE)
    crit = random.randint(1,100) <= CRIT_CHANCE
    damage = base*2 if crit else base
    desc = f"{random.choice(ATTACK_PHRASES).format(attacker_name=attacker_name,target=get_target_name(attack_target),defender_name=defender_name)}"
    if crit:
        desc = "💥 " + desc + f" (КРИТ! {damage})"
    else:
        desc += f" (Урон: {damage})"
    if attack_target == defense_target:
        damage //=2
        desc += f"\n{random.choice(DEFENSE_PHRASES).format(defender_name=defender_name,target=get_target_name(defense_target))} (Урон снижен до {damage})"
    return damage, desc
