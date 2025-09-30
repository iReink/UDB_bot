import sqlite3
import asyncio
import random
from datetime import datetime, timedelta

from aiogram import Bot, types, F, Dispatcher
from aiogram.filters.command import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from db import get_user, add_sits, get_user_display_name

# --- КОНСТАНТЫ ---
INITIAL_HEALTH = 100
MIN_DAMAGE = 16
MAX_DAMAGE = 24
CRIT_CHANCE = 5
BET_COST = 1
WIN_SITS_MIN = 1
WIN_SITS_MAX = 2
CHALLENGE_TIMEOUT_MINUTES = 10

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

# --- СОСТОЯНИЯ FSM ---
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
        "body": "туловище",
        "legs": "ноги"
    }
    return mapping.get(target_key, target_key)


# --- РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ ---
def register_fight_club_handlers(dp: Dispatcher):

    # --- Главное меню ---
    @dp.message(Command("fight"))
    async def fight_menu(message: types.Message):
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="👊 Бросить вызов (10 сита)", callback_data="fight_challenge"))
        await message.answer(
            "Добро пожаловать в Бойцовский клуб! Готов испытать свою силу и удачу?",
            reply_markup=kb.as_markup()
        )

    # --- Бросок вызова ---
    @dp.callback_query(F.data == "fight_challenge")
    async def process_fight_challenge(callback: types.CallbackQuery, state: FSMContext):
        challenger_id = callback.from_user.id
        chat_id = callback.message.chat.id
        challenger_name = get_user_display_name(challenger_id, chat_id)

        current_sits = await get_current_sits(challenger_id, chat_id)
        if current_sits < BET_COST:
            await callback.answer(f"Недостаточно сита для вызова! Нужно {BET_COST} сита.", show_alert=True)
            return

        add_sits(chat_id, challenger_id, -BET_COST)

        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(
            text=f"⚔️ Принять вызов ({BET_COST} сита)",
            callback_data=f"fight_accept_challenge:{challenger_id}"
        ))
        sent_message = await callback.message.answer(
            f"<b>{challenger_name}</b> бросил вызов! Кто готов принять бой? "
            f"Стоимость участия: {BET_COST} сита.\nВызов активен {CHALLENGE_TIMEOUT_MINUTES} минут.",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )

        await state.set_state(FightClubStates.waiting_for_challenge_acceptance)
        await state.update_data(
            challenger_id=challenger_id,
            challenger_name=challenger_name,
            chat_id=chat_id,
            challenge_message_id=sent_message.message_id,
            challenge_timestamp=datetime.now(),
            challenger_sits_at_challenge=current_sits - BET_COST,
            is_challenge_accepted=False
        )

        await callback.answer("Вызов брошен! Ожидаем соперника.", show_alert=False)

        asyncio.create_task(challenge_timeout_check(
            callback.bot, challenger_id, chat_id, sent_message.message_id, challenger_name, state
        ))

    async def challenge_timeout_check(bot: Bot, challenger_id: int, chat_id: int,
                                      message_id: int, challenger_name: str, state: FSMContext):
        await asyncio.sleep(CHALLENGE_TIMEOUT_MINUTES * 60)
        current_data = await state.get_data()
        if current_data.get("is_challenge_accepted", False):
            return
        if current_data.get("challenger_id") == challenger_id and \
           current_data.get("challenge_message_id") == message_id and \
           await state.get_state() == FightClubStates.waiting_for_challenge_acceptance:

            add_sits(chat_id, challenger_id, BET_COST)
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"<b>{challenger_name}</b> бросил вызов, но никто не осмелился принять его! "
                     f"Ставка возвращена.",
                parse_mode="HTML"
            )
            await state.clear()


    # --- Принятие вызова ---
    @dp.callback_query(F.data.startswith("fight_accept_challenge:"))
    async def process_accept_challenge(callback: types.CallbackQuery, state: FSMContext):
        accepter_id = callback.from_user.id
        chat_id = callback.message.chat.id
        _, challenger_id_str = callback.data.split(":")
        challenger_id = int(challenger_id_str)

        if accepter_id == challenger_id:
            await callback.answer("Нельзя принять свой вызов!", show_alert=True)
            return

        challenger_ctx = FSMContext(
            storage=state.storage,
            key=StorageKey(bot_id=callback.bot.id, chat_id=chat_id, user_id=challenger_id)
        )
        challenger_data = await challenger_ctx.get_data()
        if challenger_data.get("is_challenge_accepted", False) or \
           await challenger_ctx.get_state() != FightClubStates.waiting_for_challenge_acceptance:
            await callback.answer("Этот вызов уже неактивен.", show_alert=True)
            return

        accepter_name = get_user_display_name(accepter_id, chat_id)
        current_sits = await get_current_sits(accepter_id, chat_id)
        if current_sits < BET_COST:
            await callback.answer(f"Недостаточно сита для принятия вызова!", show_alert=True)
            return

        add_sits(chat_id, accepter_id, -BET_COST)

        fight_data = {
            "player1_id": challenger_id,
            "player1_name": challenger_data["challenger_name"],
            "player1_health": INITIAL_HEALTH,
            "player1_action": {},
            "player2_id": accepter_id,
            "player2_name": accepter_name,
            "player2_health": INITIAL_HEALTH,
            "player2_action": {},
            "current_round": 1,
            "chat_id": chat_id
        }

        await challenger_ctx.update_data(is_challenge_accepted=True, **fight_data)
        await challenger_ctx.set_state(FightClubStates.choosing_attack)
        await state.update_data(**fight_data)
        await state.set_state(FightClubStates.choosing_attack)

        await callback.message.edit_text(
            f"<b>{fight_data['player1_name']}</b> бросил вызов!\n"
            f"<b>{fight_data['player2_name']}</b> принял его! Бой начинается!",
            reply_markup=None,
            parse_mode="HTML"
        )

        # Отправляем оба сообщения на выбор атаки и защиты
        await ask_for_choices(callback.bot, chat_id, challenger_id, fight_data['player1_name'], challenger_ctx)
        await ask_for_choices(callback.bot, chat_id, accepter_id, fight_data['player2_name'], state)
        await callback.answer("Бой начинается!", show_alert=False)

    # --- Обработчик выбора атаки и защиты ---
    @dp.callback_query(F.data.startswith("fight_attack:") | F.data.startswith("fight_defense:"))
    async def process_choice(callback: types.CallbackQuery, state: FSMContext):
        parts = callback.data.split(":")
        action_type = parts[0].split("_")[1] if "_" in parts[0] else parts[0]
        target = parts[1]
        player_id = int(parts[2])

        fight_data = await state.get_data()
        if player_id not in [fight_data.get("player1_id"), fight_data.get("player2_id")]:
            await callback.answer("Это не ваш ход!", show_alert=True)
            return
        if player_id != callback.from_user.id:
            await callback.answer("Сейчас не ваш ход!", show_alert=True)
            return

        player_key = "player1_action" if player_id == fight_data["player1_id"] else "player2_action"
        action_dict = fight_data.get(player_key, {})
        action_dict[action_type] = target
        fight_data[player_key] = action_dict
        await state.update_data(fight_data)

        await callback.answer(f"Вы выбрали {action_type}: {get_target_name(target)}", show_alert=False)
        try:
            await callback.message.edit_text(
                f"Игрок {get_user_display_name(player_id, fight_data['chat_id'])} сделал выбор.", reply_markup=None)
        except Exception:
            pass

        # Если оба игрока выбрали и атаку, и защиту, рассчитываем раунд
        p1 = fight_data.get("player1_action", {})
        p2 = fight_data.get("player2_action", {})
        if all(k in p1 for k in ["attack", "defense"]) and all(k in p2 for k in ["attack", "defense"]):
            await calculate_round_results(callback.bot, fight_data, state)


# --- Функция для отправки сообщений с кнопками ---
async def ask_for_choices(bot: Bot, chat_id: int, user_id: int, user_name: str, state: FSMContext):
    # Сообщение для атаки
    kb_attack = InlineKeyboardBuilder()
    kb_attack.row(
        InlineKeyboardButton(text="🎯 Голова", callback_data=f"fight_attack:head:{user_id}"),
        InlineKeyboardButton(text="💪 Тело", callback_data=f"fight_attack:body:{user_id}"),
        InlineKeyboardButton(text="🦵 Ноги", callback_data=f"fight_attack:legs:{user_id}")
    )
    await bot.send_message(chat_id, f"Игрок {user_name}, выберите цель для атаки:", reply_markup=kb_attack.as_markup())

    # Сообщение для защиты
    kb_defense = InlineKeyboardBuilder()
    kb_defense.row(
        InlineKeyboardButton(text="🛡️ Голова", callback_data=f"fight_defense:head:{user_id}"),
        InlineKeyboardButton(text="🛡️ Тело", callback_data=f"fight_defense:body:{user_id}"),
        InlineKeyboardButton(text="🛡️ Ноги", callback_data=f"fight_defense:legs:{user_id}")
    )
    await bot.send_message(chat_id, f"Игрок {user_name}, выберите цель для защиты:", reply_markup=kb_defense.as_markup())



# --- Функция расчета результатов раунда ---
async def calculate_round_results(bot: Bot, fight_data: dict, state: FSMContext):
    p1 = fight_data["player1_action"]
    p2 = fight_data["player2_action"]
    p1_health = fight_data["player1_health"]
    p2_health = fight_data["player2_health"]
    p1_name = fight_data["player1_name"]
    p2_name = fight_data["player2_name"]
    chat_id = fight_data["chat_id"]

    # Урон игроку 2
    damage2, desc1 = calculate_damage(p1_name, p2_name, p1["attack"], p2["defense"])
    p2_health -= damage2
    # Урон игроку 1
    damage1, desc2 = calculate_damage(p2_name, p1_name, p2["attack"], p1["defense"])
    p1_health -= damage1

    fight_data["player1_health"] = p1_health
    fight_data["player2_health"] = p2_health

    # Сообщение о раунде
    round_text = (
        f"<b>--- Раунд {fight_data['current_round']} ---</b>\n"
        f"{desc1}\n{desc2}\n\n"
        f"Здоровье {p1_name}: {max(0, p1_health)} ❤️\n"
        f"Здоровье {p2_name}: {max(0, p2_health)} ❤️"
    )
    await bot.send_message(chat_id, round_text, parse_mode="HTML")

    # Проверка победителя
    if p1_health <= 0 or p2_health <= 0:
        winner_id = fight_data["player1_id"] if p2_health <= 0 else fight_data["player2_id"]
        loser_id = fight_data["player2_id"] if p2_health <= 0 else fight_data["player1_id"]
        winner_name = get_user_display_name(winner_id, chat_id)
        win_sits_amount = random.randint(WIN_SITS_MIN, WIN_SITS_MAX)
        add_sits(chat_id, winner_id, BET_COST + win_sits_amount)
        await bot.send_message(chat_id,
                               f"🏆 Победитель: {winner_name}!\n💰 Получает {BET_COST + win_sits_amount} сита!",
                               parse_mode="HTML")
        await state.clear()
    else:
        # Сброс действий и следующий раунд
        fight_data["player1_action"] = {}
        fight_data["player2_action"] = {}
        fight_data["current_round"] += 1
        await state.update_data(fight_data)
        # Запрос новых действий
        await ask_for_choices(bot, chat_id, fight_data["player1_id"], fight_data["player1_name"], state)
        await ask_for_choices(bot, chat_id, fight_data["player2_id"], fight_data["player2_name"], state)


# --- Функция расчета урона ---
def calculate_damage(attacker_name: str, defender_name: str, attack_target: str, defense_target: str):
    base_damage = random.randint(MIN_DAMAGE, MAX_DAMAGE)
    is_crit = random.randint(1, 100) <= CRIT_CHANCE
    damage = base_damage * 2 if is_crit else base_damage
    attack_desc = f"{random.choice(ATTACK_PHRASES).format(attacker_name=attacker_name, target=get_target_name(attack_target), defender_name=defender_name)}"
    if is_crit:
        attack_desc += f" (КРИТ! Урон: {damage})"
    else:
        attack_desc += f" (Урон: {damage})"
    if attack_target == defense_target:
        damage //= 2
        attack_desc += f"\n{random.choice(DEFENSE_PHRASES).format(defender_name=defender_name, target=get_target_name(defense_target))} (Урон снижен до {damage})"
    return damage, attack_desc
