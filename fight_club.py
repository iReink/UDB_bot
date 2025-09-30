import sqlite3
import asyncio
import random
from datetime import datetime, timedelta

from aiogram import types, F, Router
from aiogram.filters.command import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db import get_user, add_sits, get_user_display_name, DB_PATH # Импорт функций для работы с БД

router = Router()

# --- КОНСТАНТЫ ИГРЫ ---
INITIAL_HEALTH = 100
MIN_DAMAGE = 8
MAX_DAMAGE = 12
CRIT_CHANCE = 5 # Процент
BET_COST = 10 # Стоимость вызова/принятия в сита
WIN_SITS_MIN = 2
WIN_SITS_MAX = 8
CHALLENGE_TIMEOUT_MINUTES = 10 # Таймаут на принятие вызова

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
    waiting_for_challenge_acceptance = State() # Ожидание, пока кто-то примет вызов
    choosing_attack_target = State() # Выбор цели для атаки
    choosing_defense_target = State() # Выбор цели для защиты
    # Дополнительные состояния могут понадобиться для конкретизации игрока
    # Например, если игроки выбирают одновременно, но бот обрабатывает по очереди
    choosing_attack_challenger = State()
    choosing_defense_challenger = State()
    choosing_attack_accepter = State()
    choosing_defense_accepter = State()


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

# --- ГЛАВНОЕ МЕНЮ БОЙЦОВСКОГО КЛУБА ---
@router.message(Command("fight"))
async def fight_menu(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="👊 Бросить вызов (10 сита)", callback_data="fight_challenge"))
    
    await message.answer(
        "Добро пожаловать в Бойцовский клуб! Готов испытать свою силу и удачу?",
        reply_markup=kb.as_markup()
    )

# --- ОБРАБОТЧИК БРОСАНИЯ ВЫЗОВА ---
@router.callback_query(F.data == "fight_challenge")
async def process_fight_challenge(callback: types.CallbackQuery, state: FSMContext):
    challenger_id = callback.from_user.id
    chat_id = callback.message.chat.id
    challenger_name = await get_user_display_name(challenger_id, chat_id)
    
    current_sits = await get_current_sits(challenger_id, chat_id)
    
    if current_sits < BET_COST:
        await callback.answer(f"Недостаточно сита для вызова! Нужно {BET_COST} сита.", show_alert=True)
        return
        
    add_sits(chat_id, challenger_id, -BET_COST) # Снимаем ставку
    
    challenge_message_text = (
        f"<b>{challenger_name}</b> бросил вызов в Бойцовский клуб! 💪 Кто готов принять бой? "
        f"Стоимость участия: {BET_COST} сита. У тебя {current_sits - BET_COST} сита.\n\n"
        f"Вызов активен {CHALLENGE_TIMEOUT_MINUTES} минут."
    )
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=f"⚔️ Принять вызов ({BET_COST} сита)", callback_data=f"fight_accept_challenge:{challenger_id}"))
    
    sent_message = await callback.message.answer(
        challenge_message_text,
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    
    # Сохраняем информацию о вызове в FSM
    await state.set_state(FightClubStates.waiting_for_challenge_acceptance)
    await state.update_data(
        challenger_id=challenger_id,
        challenger_name=challenger_name,
        chat_id=chat_id,
        challenge_message_id=sent_message.message_id,
        challenge_timestamp=datetime.now(),
        challenger_sits_at_challenge=current_sits - BET_COST # Сит после ставки
    )
    
    await callback.answer("Вызов брошен! Ожидаем соперника.", show_alert=False)
    
    # Запускаем таймер для отмены вызова
    asyncio.create_task(challenge_timeout_check(
        callback.bot, challenger_id, chat_id, sent_message.message_id, challenger_name, state
    ))

async def challenge_timeout_check(bot: types.Bot, challenger_id: int, chat_id: int, message_id: int, challenger_name: str, state: FSMContext):
    await asyncio.sleep(CHALLENGE_TIMEOUT_MINUTES * 60) # Ждем 10 минут
    
    current_data = await state.get_data()
    # Проверяем, не был ли вызов уже принят
    if current_data.get("challenger_id") == challenger_id and \
       current_data.get("challenge_message_id") == message_id and \
       await state.get_state() == FightClubStates.waiting_for_challenge_acceptance:
        
        # Возвращаем ситы вызывающему
        add_sits(chat_id, challenger_id, BET_COST)
        
        # Обновляем сообщение
        bot_instance = current_data.get("bot_instance") # Нужно передать экземпляр бота
        if bot_instance:
            await bot_instance.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"<b>{challenger_name}</b> бросал вызов, но никто не осмелился принять его! 😥 "
                     f"Ставка в {BET_COST} сита возвращена.\n\n"
                     f"Его текущий баланс: {current_data.get('challenger_sits_at_challenge', 0) + BET_COST} сита.",
                parse_mode="HTML"
            )
        else:
            # Fallback, если bot_instance не был сохранен в FSM (нужно будет убедиться, что он есть)
            # В реальном сценарии bot_instance должен быть доступен
            pass 
        
        await state.clear() # Очищаем состояние FSM
        
# --- ОБРАБОТЧИК ПРИНЯТИЯ ВЫЗОВА ---
@router.callback_query(F.data.startswith("fight_accept_challenge:"))
async def process_accept_challenge(callback: types.CallbackQuery, state: FSMContext):
    accepter_id = callback.from_user.id
    chat_id = callback.message.chat.id
    
    data = await state.get_data()
    challenger_id = data.get("challenger_id")
    challenge_message_id = data.get("challenge_message_id")
    
    if accepter_id == challenger_id:
        await callback.answer("Нельзя принимать свой собственный вызов, хитрец! 😉", show_alert=True)
        return
        
    if await state.get_state() != FightClubStates.waiting_for_challenge_acceptance or \
       challenge_message_id != callback.message.message_id:
        await callback.answer("Этот вызов уже неактивен или был принят.", show_alert=True)
        return
        
    accepter_name = await get_user_display_name(accepter_id, chat_id)
    current_sits = await get_current_sits(accepter_id, chat_id)
    
    if current_sits < BET_COST:
        await callback.answer(f"Недостаточно сита для принятия вызова! Нужно {BET_COST} сита.", show_alert=True)
        return
        
    add_sits(chat_id, accepter_id, -BET_COST) # Снимаем ставку
    
    # Обновляем сообщение о вызове
    challenger_name = data.get("challenger_name")
    new_message_text = (
        f"<b>{challenger_name}</b> бросил вызов!\n"
        f"<b>{accepter_name}</b> отважно принял его! 🤩 Бой начинается!\n\n"
        f"У тебя {await get_current_sits(accepter_id, chat_id)} сита."
    )
    
    await callback.message.edit_text(
        new_message_text,
        reply_markup=None, # Убираем кнопку "Принять вызов"
        parse_mode="HTML"
    )
    
    # Инициализируем бой
    fight_data = {
        "player1_id": challenger_id,
        "player1_name": challenger_name,
        "player1_health": INITIAL_HEALTH,
        "player2_id": accepter_id,
        "player2_name": accepter_name,
        "player2_health": INITIAL_HEALTH,
        "current_round": 1,
        "player1_action": None, # {'attack': 'head', 'defense': 'body'}
        "player2_action": None,
        "last_message_id": callback.message.message_id # Сохраняем id сообщения для последующих обновлений
    }
    
    await state.update_data(fight_data)
    await state.set_state(FightClubStates.choosing_attack_challenger)
    
    # Отправляем запросы на выбор действия обоим игрокам
    await ask_for_attack_choice(callback.bot, chat_id, challenger_id, challenger_name, state)
    await ask_for_attack_choice(callback.bot, chat_id, accepter_id, accepter_name, state)
    
    await callback.answer("Вызов принят! Приготовьтесь к бою!", show_alert=False)

# --- ФУНКЦИИ ДЛЯ ПОШАГОВОГО БОЯ ---
async def ask_for_attack_choice(bot: types.Bot, chat_id: int, user_id: int, user_name: str, state: FSMContext):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🎯 Голова", callback_data=f"fight_attack:head:{user_id}"))
    kb.row(InlineKeyboardButton(text="💪 Туловище", callback_data=f"fight_attack:body:{user_id}"))
    kb.row(InlineKeyboardButton(text="🦵 Ноги", callback_data=f"fight_attack:legs:{user_id}"))
    
    await bot.send_message(
        chat_id=user_id, # Отправляем в личные сообщения игроку
        text=f"Раунд { (await state.get_data()).get('current_round', 1) }. {user_name}, выберите цель для атаки:",
        reply_markup=kb.as_markup()
    )

@router.callback_query(F.data.startswith("fight_attack:"))
async def process_attack_choice(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    target = parts[1]
    player_id = int(parts[2])
    
    fight_data = await state.get_data()
    
    if player_id not in [fight_data["player1_id"], fight_data["player2_id"]]:
        await callback.answer("Это не ваш ход!", show_alert=True)
        return
        
    player_key = "player1_action" if player_id == fight_data["player1_id"] else "player2_action"
    
    # Обновляем данные FSM
    player_action = fight_data.get(player_key, {})
    player_action['attack'] = target
    fight_data[player_key] = player_action
    await state.update_data(fight_data)
    
    await callback.answer(f"Вы выбрали цель для атаки: {get_target_name(target)}", show_alert=False)
    await callback.message.edit_text(f"Игрок {await get_user_display_name(player_id, callback.message.chat.id)} выбрал цель для атаки.", reply_markup=None) # Удаляем кнопки
    
    # Проверяем, сделали ли оба игрока свой выбор атаки
    if fight_data.get("player1_action", {}).get("attack") and \
       fight_data.get("player2_action", {}).get("attack"):
        
        await state.set_state(FightClubStates.choosing_defense_challenger) # Переходим к выбору защиты
        
        # Запрашиваем выбор защиты у обоих игроков
        await ask_for_defense_choice(callback.bot, fight_data["chat_id"], fight_data["player1_id"], fight_data["player1_name"], state)
        await ask_for_defense_choice(callback.bot, fight_data["chat_id"], fight_data["player2_id"], fight_data["player2_name"], state)

async def ask_for_defense_choice(bot: types.Bot, chat_id: int, user_id: int, user_name: str, state: FSMContext):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🛡️ Голова", callback_data=f"fight_defense:head:{user_id}"))
    kb.row(InlineKeyboardButton(text="🛡️ Туловище", callback_data=f"fight_defense:body:{user_id}"))
    kb.row(InlineKeyboardButton(text="🛡️ Ноги", callback_data=f"fight_defense:legs:{user_id}"))
    
    await bot.send_message(
        chat_id=user_id, # Отправляем в личные сообщения игроку
        text=f"{user_name}, выберите, какую часть тела защищать:",
        reply_markup=kb.as_markup()
    )

@router.callback_query(F.data.startswith("fight_defense:"))
async def process_defense_choice(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    target = parts[1]
    player_id = int(parts[2])
    
    fight_data = await state.get_data()
    
    if player_id not in [fight_data["player1_id"], fight_data["player2_id"]]:
        await callback.answer("Это не ваш ход!", show_alert=True)
        return
        
    player_key = "player1_action" if player_id == fight_data["player1_id"] else "player2_action"
    
    # Обновляем данные FSM
    player_action = fight_data.get(player_key, {})
    player_action['defense'] = target
    fight_data[player_key] = player_action
    await state.update_data(fight_data)
    
    await callback.answer(f"Вы выбрали защиту: {get_target_name(target)}", show_alert=False)
    await callback.message.edit_text(f"Игрок {await get_user_display_name(player_id, callback.message.chat.id)} выбрал что хочет защитить.", reply_markup=None) # Удаляем кнопки
    
    # Проверяем, сделали ли оба игрока свой выбор защиты
    if fight_data.get("player1_action", {}).get("defense") and \
       fight_data.get("player2_action", {}).get("defense"):
        
        await calculate_round_results(callback.bot, fight_data["chat_id"], state)

async def calculate_round_results(bot: types.Bot, chat_id: int, state: FSMContext):
    fight_data = await state.get_data()
    
    p1_id = fight_data["player1_id"]
    p2_id = fight_data["player2_id"]
    p1_name = fight_data["player1_name"]
    p2_name = fight_data["player2_name"]
    p1_health = fight_data["player1_health"]
    p2_health = fight_data["player2_health"]
    
    p1_action = fight_data["player1_action"]
    p2_action = fight_data["player2_action"]
    
    results = []
    
    # Расчет урона для Player 1
    damage_to_p2, p1_attack_desc = calculate_damage(p1_name, p2_name, p1_action["attack"], p2_action["defense"])
    p2_health -= damage_to_p2
    results.append(p1_attack_desc)
    
    # Расчет урона для Player 2
    damage_to_p1, p2_attack_desc = calculate_damage(p2_name, p1_name, p2_action["attack"], p1_action["defense"])
    p1_health -= damage_to_p1
    results.append(p2_attack_desc)
    
    fight_data["player1_health"] = p1_health
    fight_data["player2_health"] = p2_health
    
    # Сообщение о текущем состоянии боя
    fight_summary_text = (
        f"<b>--- Раунд {fight_data['current_round']} ---</b>\n\n"
        f"{results[0]}\n"
        f"{results[1]}\n\n"
        f"Здоровье <b>{p1_name}</b>: {max(0, p1_health)} ❤️\n"
        f"Здоровье <b>{p2_name}</b>: {max(0, p2_health)} ❤️"
    )
    
    await bot.send_message(
        chat_id=chat_id,
        text=fight_summary_text,
        parse_mode="HTML"
    )
    
    # Проверка условий победы/поражения
    winner_id = None
    loser_id = None
    if p1_health <= 0 and p2_health <= 0:
        # Оба проиграли, побеждает бросивший вызов (Player 1)
        winner_id = p1_id
        loser_id = p2_id
        await bot.send_message(
            chat_id=chat_id,
            text=f"Оба бойца пали в этом безжалостном поединке! 😵‍💫 Но по правилам клуба, победа присуждается первому, кто бросил вызов!\n\n"
                 f"🎉 Победитель: <b>{p1_name}</b>!",
            parse_mode="HTML"
        )
    elif p1_health <= 0:
        winner_id = p2_id
        loser_id = p1_id
        await bot.send_message(
            chat_id=chat_id,
            text=f"<b>{p1_name}</b> не выдержал натиска! 😫\n\n"
                 f"🏆 Победитель: <b>{p2_name}</b>!",
            parse_mode="HTML"
        )
    elif p2_health <= 0:
        winner_id = p1_id
        loser_id = p2_id
        await bot.send_message(
            chat_id=chat_id,
            text=f"<b>{p2_name}</b> повержен! 😩\n\n"
                 f"🏆 Победитель: <b>{p1_name}</b>!",
            parse_mode="HTML"
        )
        
    if winner_id:
        # Распределение сит
        win_sits_amount = random.randint(WIN_SITS_MIN, WIN_SITS_MAX)
        add_sits(chat_id, winner_id, BET_COST + win_sits_amount) # Возвращаем ставку + приз
        # Проигравший теряет ставку (она уже снята)
        
        await bot.send_message(
            chat_id=chat_id,
            text=f"💰 {await get_user_display_name(winner_id, chat_id)} получает {BET_COST + win_sits_amount} сита!"
                 f" (Возвращена ставка: {BET_COST}, Выигрыш: {win_sits_amount})\n"
                 f"Текущий баланс: {await get_current_sits(winner_id, chat_id)} сита.",
            parse_mode="HTML"
        )
        
        await state.clear() # Завершаем бой
    else:
        # Продолжаем бой
        fight_data["current_round"] += 1
        fight_data["player1_action"] = None
        fight_data["player2_action"] = None
        await state.update_data(fight_data)
        
        await state.set_state(FightClubStates.choosing_attack_challenger)
        await ask_for_attack_choice(bot, chat_id, p1_id, p1_name, state)
        await ask_for_attack_choice(bot, chat_id, p2_id, p2_name, state)
        
def calculate_damage(attacker_name: str, defender_name: str, attack_target: str, defense_target: str):
    base_damage = random.randint(MIN_DAMAGE, MAX_DAMAGE)
    is_crit = random.randint(1, 100) <= CRIT_CHANCE
    
    if is_crit:
        damage = base_damage * 2
        attack_desc = f"💥 {random.choice(ATTACK_PHRASES).format(attacker_name=attacker_name, target=get_target_name(attack_target), defender_name=defender_name)} (КРИТ! Урон: {damage})"
    else:
        damage = base_damage
        attack_desc = f"{random.choice(ATTACK_PHRASES).format(attacker_name=attacker_name, target=get_target_name(attack_target), defender_name=defender_name)} (Урон: {damage})"
        
    if attack_target == defense_target:
        damage //= 2 # Урон пополам, если защита успешна
        attack_desc += f"\n{random.choice(DEFENSE_PHRASES).format(defender_name=defender_name, target=get_target_name(defense_target))} (Урон снижен до {damage}!)"
        
    return damage, attack_desc
