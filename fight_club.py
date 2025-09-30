import sqlite3
import asyncio
import random
import logging
from datetime import datetime, timedelta

from aiogram import Bot, types, F, Dispatcher
from aiogram.filters.command import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import TelegramRetryAfter

from db import get_user, add_sits, get_user_display_name

# --- КОНСТАНТЫ ИГРЫ ---
INITIAL_HEALTH = 100
MIN_DAMAGE = 16
MAX_DAMAGE = 24
CRIT_CHANCE = 5  # Процент
BET_COST = 10  # Стоимость вызова/принятия в сита
WIN_SITS_MIN = 2
WIN_SITS_MAX = 8
CHALLENGE_TIMEOUT_MINUTES = 10  # Таймаут на принятие вызова

# --- ФРАЗЫ ДЛЯ АТАКИ ---
ATTACK_PHRASES = [
    "{attacker_name} бьёт ногой с разворота в {target} {defender_name}!",
    "{attacker_name} со всей дури колошматит по {target} {defender_name}!",
    "{attacker_name} деликатно тыкает в {target} {defender_name}!",
    "{attacker_name} наносит сокрушительный удар в {target} {defender_name}!",
    "{attacker_name} мастерски пробивает {defender_name} в {target}!",
    "{attacker_name} финтом отправляет кулак в {target} {defender_name}!"
]

# --- ФРАЗЫ ДЛЯ ЗАЩИТЫ ---
DEFENSE_PHRASES = [
    "{defender_name} прикрывает {target}.",
    "{defender_name} не даёт в обиду {target}.",
    "{defender_name} бережёт {target} смолоду.",
    "{defender_name} успевает поставить блок на {target}.",
    "{defender_name} уклоняется от удара в {target}.",
    "{defender_name} принимает удар в {target} на защиту!"
]

# --- СОСТОЯНИЯ FSM ДЛЯ БОЯ ---
class FightClubStates(StatesGroup):
    waiting_for_challenge_acceptance = State()
    choosing_attack_challenger = State()
    choosing_defense_challenger = State()
    choosing_attack_accepter = State()
    choosing_defense_accepter = State()


# --- УТИЛИТЫ ---
async def safe_send_message(bot, chat_id, text, **kwargs):
    """Безопасная отправка сообщений с защитой от flood control"""
    try:
        return await bot.send_message(chat_id, text, **kwargs)
    except TelegramRetryAfter as e:
        logging.warning(f"Flood control: ждём {e.retry_after} секунд")
        await asyncio.sleep(e.retry_after)
        return await bot.send_message(chat_id, text, **kwargs)
    except Exception as ex:
        logging.error(f"Ошибка при отправке сообщения: {ex}")

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

def register_fight_club_handlers(dp: Dispatcher):
    # --- ГЛАВНОЕ МЕНЮ ---
    @dp.message(Command("fight"))
    async def fight_menu(message: types.Message, state: FSMContext):
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="👊 Бросить вызов (10 сита)", callback_data="fight_challenge"))
        await message.answer(
            "Добро пожаловать в Бойцовский клуб! Готов испытать свою силу и удачу?",
            reply_markup=kb.as_markup()
        )

    # --- ВЫЗОВ ---
    @dp.callback_query(F.data == "fight_challenge")
    async def process_fight_challenge(callback: types.CallbackQuery, state: FSMContext):
        challenger_id = callback.from_user.id
        chat_id = callback.message.chat.id
        challenger_name = get_user_display_name(challenger_id, chat_id)

        current_sits = await get_current_sits(challenger_id, chat_id)
        if current_sits < BET_COST:
            await callback.answer(f"Недостаточно сита (нужно {BET_COST})", show_alert=True)
            return

        add_sits(chat_id, challenger_id, -BET_COST)

        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(
            text=f"⚔️ Принять вызов ({BET_COST} сита)",
            callback_data=f"fight_accept_challenge:{challenger_id}"
        ))

        sent_message = await safe_send_message(
            callback.bot,
            chat_id,
            f"<b>{challenger_name}</b> бросил вызов! 💪\nСтоимость участия: {BET_COST} сита\n\n"
            f"Вызов активен {CHALLENGE_TIMEOUT_MINUTES} минут.",
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
        await callback.answer("Вызов брошен!")

        asyncio.create_task(challenge_timeout_check(
            callback.bot, challenger_id, chat_id, sent_message.message_id, challenger_name, state
        ))

    async def challenge_timeout_check(bot: Bot, challenger_id: int, chat_id: int, message_id: int, challenger_name: str, state: FSMContext):
        await asyncio.sleep(CHALLENGE_TIMEOUT_MINUTES * 60)
        current_data = await state.get_data()
        if current_data.get("is_challenge_accepted", False):
            return
        if current_data.get("challenger_id") == challenger_id and \
           current_data.get("challenge_message_id") == message_id and \
           await state.get_state() == FightClubStates.waiting_for_challenge_acceptance:
            add_sits(chat_id, challenger_id, BET_COST)
            await safe_send_message(
                bot, chat_id,
                f"<b>{challenger_name}</b> ждал соперника, но никто не вышел! 😥\nСтавка возвращена.",
                parse_mode="HTML"
            )
            await state.clear()

    # --- ПРИНЯТИЕ ВЫЗОВА ---
    @dp.callback_query(F.data.startswith("fight_accept_challenge:"))
    async def process_accept_challenge(callback: types.CallbackQuery, state: FSMContext):
        accepter_id = callback.from_user.id
        chat_id = callback.message.chat.id
        challenger_id = int(callback.data.split(":")[1])

        if accepter_id == challenger_id:
            await callback.answer("Нельзя драться с самим собой!", show_alert=True)
            return

        accepter_name = get_user_display_name(accepter_id, chat_id)
        current_sits = await get_current_sits(accepter_id, chat_id)
        if current_sits < BET_COST:
            await callback.answer("Недостаточно сита!", show_alert=True)
            return

        add_sits(chat_id, accepter_id, -BET_COST)

        challenger_name = get_user_display_name(challenger_id, chat_id)
        fight_data = {
            "player1_id": challenger_id,
            "player1_name": challenger_name,
            "player1_health": INITIAL_HEALTH,
            "player2_id": accepter_id,
            "player2_name": accepter_name,
            "player2_health": INITIAL_HEALTH,
            "current_round": 1,
            "player1_action": {},
            "player2_action": {},
            "chat_id": chat_id
        }

        await state.update_data(**fight_data)
        await state.set_state(FightClubStates.choosing_attack_accepter)

        await callback.message.edit_text(
            f"<b>{challenger_name}</b> и <b>{accepter_name}</b> выходят на бой! ⚔️",
            parse_mode="HTML"
        )

        await ask_for_actions(callback.bot, chat_id, fight_data)

    # --- ВЫБОР АТАКИ/ЗАЩИТЫ ---
    async def ask_for_actions(bot: Bot, chat_id: int, fight_data: dict):
        round_num = fight_data["current_round"]

        atk_kb = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="🎯 Голова", callback_data="fight_attack:head"),
                InlineKeyboardButton(text="💪 Тело", callback_data="fight_attack:body"),
                InlineKeyboardButton(text="🦵 Ноги", callback_data="fight_attack:legs"),
            ]]
        )
        def_kb = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="🛡️ Голова", callback_data="fight_defense:head"),
                InlineKeyboardButton(text="🛡️ Тело", callback_data="fight_defense:body"),
                InlineKeyboardButton(text="🛡️ Ноги", callback_data="fight_defense:legs"),
            ]]
        )

        await safe_send_message(bot, chat_id, f"Раунд {round_num}. Выберите куда атаковать:", reply_markup=atk_kb)
        await safe_send_message(bot, chat_id, f"Раунд {round_num}. Выберите что защитить:", reply_markup=def_kb)

    @dp.callback_query(F.data.startswith("fight_attack:"))
    async def process_attack_choice(callback: types.CallbackQuery, state: FSMContext):
        target = callback.data.split(":")[1]
        fight_data = await state.get_data()
        user_id = callback.from_user.id

        if user_id not in [fight_data["player1_id"], fight_data["player2_id"]]:
            await callback.answer("Ты не в этом бою!", show_alert=True)
            return

        key = "player1_action" if user_id == fight_data["player1_id"] else "player2_action"
        fight_data[key]["attack"] = target
        await state.update_data(**fight_data)
        await callback.answer(f"Атака: {get_target_name(target)}")

        await check_round_end(callback.bot, state, fight_data)

    @dp.callback_query(F.data.startswith("fight_defense:"))
    async def process_defense_choice(callback: types.CallbackQuery, state: FSMContext):
        target = callback.data.split(":")[1]
        fight_data = await state.get_data()
        user_id = callback.from_user.id

        if user_id not in [fight_data["player1_id"], fight_data["player2_id"]]:
            await callback.answer("Ты не в этом бою!", show_alert=True)
            return

        key = "player1_action" if user_id == fight_data["player1_id"] else "player2_action"
        fight_data[key]["defense"] = target
        await state.update_data(**fight_data)
        await callback.answer(f"Защита: {get_target_name(target)}")

        await check_round_end(callback.bot, state, fight_data)

    async def check_round_end(bot: Bot, state: FSMContext, fight_data: dict):
        p1 = fight_data["player1_action"]
        p2 = fight_data["player2_action"]

        if "attack" in p1 and "defense" in p1 and "attack" in p2 and "defense" in p2:
            await calculate_round_results(bot, state, fight_data)

    async def calculate_round_results(bot: Bot, state: FSMContext, fight_data: dict):
        p1_name = fight_data["player1_name"]
        p2_name = fight_data["player2_name"]
        p1_health = fight_data["player1_health"]
        p2_health = fight_data["player2_health"]
        p1_action = fight_data["player1_action"]
        p2_action = fight_data["player2_action"]

        dmg2, desc1 = calculate_damage(p1_name, p2_name, p1_action["attack"], p2_action["defense"])
        dmg1, desc2 = calculate_damage(p2_name, p1_name, p2_action["attack"], p1_action["defense"])

        p1_health -= dmg1
        p2_health -= dmg2

        text = (
            f"<b>--- Раунд {fight_data['current_round']} ---</b>\n\n"
            f"{desc1}\n{desc2}\n\n"
            f"{p1_name}: {max(0, p1_health)} ❤️\n"
            f"{p2_name}: {max(0, p2_health)} ❤️"
        )
        await safe_send_message(bot, fight_data["chat_id"], text, parse_mode="HTML")

        if p1_health <= 0 or p2_health <= 0:
            winner, loser = None, None
            if p1_health <= 0 and p2_health <= 0:
                winner = fight_data["player1_id"]
                await safe_send_message(bot, fight_data["chat_id"], f"Оба пали! Победа {p1_name}")
            elif p1_health <= 0:
                winner = fight_data["player2_id"]
                await safe_send_message(bot, fight_data["chat_id"], f"{p1_name} пал! Победа {p2_name}")
            elif p2_health <= 0:
                winner = fight_data["player1_id"]
                await safe_send_message(bot, fight_data["chat_id"], f"{p2_name} пал! Победа {p1_name}")

            if winner:
                prize = BET_COST + random.randint(WIN_SITS_MIN, WIN_SITS_MAX)
                add_sits(fight_data["chat_id"], winner, prize)
                await safe_send_message(bot, fight_data["chat_id"], f"💰 Победитель получает {prize} сита!")
            await state.clear()
        else:
            fight_data["player1_health"] = p1_health
            fight_data["player2_health"] = p2_health
            fight_data["player1_action"] = {}
            fight_data["player2_action"] = {}
            fight_data["current_round"] += 1
            await state.update_data(**fight_data)
            await ask_for_actions(bot, fight_data["chat_id"], fight_data)


def calculate_damage(attacker_name: str, defender_name: str, attack_target: str, defense_target: str):
    base_damage = random.randint(MIN_DAMAGE, MAX_DAMAGE)
    is_crit = random.randint(1, 100) <= CRIT_CHANCE
    damage = base_damage * 2 if is_crit else base_damage

    desc = f"{random.choice(ATTACK_PHRASES).format(attacker_name=attacker_name, target=get_target_name(attack_target), defender_name=defender_name)}"
    if is_crit:
        desc = "💥 " + desc + f" (КРИТ! {damage})"
    else:
        desc = desc + f" (Урон: {damage})"

    if attack_target == defense_target:
        damage //= 2
        desc += f"\n{random.choice(DEFENSE_PHRASES).format(defender_name=defender_name, target=get_target_name(defense_target))} (Урон снижен до {damage})"

    return damage, desc
