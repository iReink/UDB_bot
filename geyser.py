# geyser.py
import asyncio
import logging
import random
from datetime import datetime, time, timedelta, date
from aiogram import Bot, Dispatcher, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardButton
from contextlib import closing

from db import get_connection, get_all_chats, add_or_update_user, add_geyser_event, get_pending_geyser_events, update_geyser_event_status, update_geyser_event_message_id, add_sits # Добавляем add_sits
from settings import get_setting # Для проверки включения гейзера

# --- Константы ---
GEYSER_MESSAGE = "Гейзер с ситом рванул!"
GEYSER_BUTTON_TEXT = "Подставить ведёрко"
GEYSER_TIMEOUT = timedelta(minutes=10)
GEYSER_SIT_REWARD = 5

# --- Утилиты для работы с БД (geyser_events) ---
def get_geyser_count(chat_id: int, date_str: str) -> int:
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT count FROM geyser_events WHERE chat_id=? AND date=?", (chat_id, date_str))
        row = cur.fetchone()
        return row["count"] if row else 0

def increment_geyser_count(chat_id: int, date_str: str):
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO geyser_events (chat_id, date, count)
            VALUES (?, ?, 1)
            ON CONFLICT(chat_id, date) DO UPDATE SET count = geyser_events.count + 1
        """, (chat_id, date_str))
        conn.commit()

# --- Логика планирования гейзеров ---
async def schedule_daily_geysers(bot: Bot):
    while True: # Добавляем бесконечный цикл
        now = datetime.now()
        # Время для планирования - 07:00
        planning_time = now.replace(hour=7, minute=0, second=0, microsecond=0)

        # Если текущее время уже после 07:00, планируем на завтра
        if now >= planning_time:
            planning_time += timedelta(days=1)

        # Ожидаем до следующего времени планирования
        wait_seconds = (planning_time - now).total_seconds()
        logging.info(f"[Geyser Scheduler] Следующее планирование гейзеров через {timedelta(seconds=int(wait_seconds))}")
        await asyncio.sleep(wait_seconds)

        # После пробуждения, планируем гейзеры на текущий день
        today_str = date.today().isoformat()
        chats = get_all_chats() # Получаем все чаты из БД

        for chat_id in chats:
            if get_setting(chat_id, "enable_geyser"):
                # Проверяем, были ли уже запланированы гейзеры на сегодня для этого чата
                # Если есть записи со статусом 'pending' или 'sent' на сегодня, не планируем новые
                with closing(get_connection()) as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT COUNT(*) FROM geyser_events WHERE chat_id=? AND date=? AND status IN ('pending', 'sent')", (chat_id, today_str))
                    existing_geysers_count = cur.fetchone()[0]
                
                if existing_geysers_count == 0: # Если нет запланированных гейзеров на сегодня
                    # Генерируем 3 случайных времени с 8 до 24, не чаще чем раз в час
                    scheduled_times = []
                    for _ in range(3):
                        while True:
                            random_hour = random.randint(8, 23) # С 8 до 23 включительно
                            random_minute = random.randint(0, 59)
                            new_time = time(random_hour, random_minute)

                            is_valid = True
                            for sch_time in scheduled_times:
                                # Проверяем, чтобы разница была не менее 1 часа (3600 секунд)
                                dt1 = datetime.combine(date.today(), new_time)
                                dt2 = datetime.combine(date.today(), sch_time)
                                if abs((dt1 - dt2).total_seconds()) < 3600:
                                    is_valid = False
                                    break
                            if is_valid:
                                scheduled_times.append(new_time)
                                break
                    
                    scheduled_times.sort()
                    for t in scheduled_times:
                        add_geyser_event(chat_id, today_str, t.strftime("%H:%M"), 'pending')
                    logging.info(f"[Geyser] Запланированы гейзеры для чата {chat_id} на {today_str}: {[t.strftime('%H:%M') for t in scheduled_times]}")

# --- Хэндлеры для гейзера ---
async def handle_geyser_catch(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    user_name = callback.from_user.full_name or str(user_id)
    message_id = callback.message.message_id
    event_id = int(callback.data.split(":")[1]) # Извлекаем event_id из callback_data

    if message_id not in active_geysers: # Проверяем по message_id
        await callback.answer("❌ Этот гейзер уже неактивен!", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        return

    geyser_data = active_geysers[message_id] # Доступ по message_id
    if geyser_data["winner_id"] is not None: # Кто-то уже поймал
        await callback.answer("❌ Кто-то уже успел поймать сито!", show_alert=True)
        return
    
    # Назначаем победителя и отменяем таймаут
    geyser_data["winner_id"] = user_id
    if geyser_data["timeout_task"]:
        geyser_data["timeout_task"].cancel()
    
    # Обновляем статус в БД на 'caught'
    update_geyser_event_status(event_id, 'caught')

    # Удаляем кнопку и отправляем сообщение о победе
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(f"@{user_name} успешно поймал {GEYSER_SIT_REWARD} сита в ведёрко!")
    except Exception as e:
        logging.error(f"[Geyser] Ошибка при обновлении сообщения гейзера или отправке победы: {e}")

    # Увеличиваем баланс сит
    # add_or_update_user, но только для sits
    # with closing(get_connection()) as conn:
    #     cur = conn.cursor()
    #     cur.execute("""
    #         UPDATE users SET sits = sits + ? WHERE user_id = ? AND chat_id = ?
    #     """, (GEYSER_SIT_REWARD, user_id, chat_id))
    #     conn.commit()
    add_sits(chat_id, user_id, GEYSER_SIT_REWARD) # Используем новую функцию add_sits

    logging.info(f"[Geyser] Пользователь {user_name} ({user_id}) поймал гейзер в чате {chat_id}, event_id: {event_id}")
    del active_geysers[message_id] # Удаляем из активных гейзеров
    await callback.answer() # Закрываем callback

def register_geyser_handlers(dp: Dispatcher):
    dp.callback_query.register(handle_geyser_catch, F.data.startswith("geyser_catch:"))

# --- Текущие активные гейзеры (для отслеживания сообщений и таймеров) ---
active_geysers = {} # {chat_id: {message_id: {timeout_task, winner_id, ...}}}

# --- Логика тайм-аута гейзера ---
async def _geyser_timeout_task(bot: Bot, chat_id: int, message_id: int, event_id: int):
    await asyncio.sleep(GEYSER_TIMEOUT.total_seconds())

    if message_id in active_geysers: # Проверяем, есть ли гейзер по message_id
        geyser_data = active_geysers[message_id]
        if geyser_data["winner_id"] is None: # Никто не поймал
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="Гейзер угас, но будут ещё!",
                    reply_markup=None
                )
                logging.info(f"[Geyser] Гейзер {message_id} в чате {chat_id} угас без победителя, event_id: {event_id}.")
                update_geyser_event_status(event_id, 'expired') # Обновляем статус в БД
            except Exception as e:
                logging.error(f"[Geyser] Ошибка при изменении сообщения гейзера по таймауту: {e}")
        
        del active_geysers[message_id] # Удаляем гейзер из активных по message_id

# --- Фоновая задача для запуска гейзеров ---
async def geyser_loop_task(bot: Bot):
    while True:
        today_str = date.today().isoformat()
        pending_events = get_pending_geyser_events(today_str)
        now = datetime.now().time()

        for event in pending_events:
            scheduled_time = datetime.strptime(event["scheduled_time"], "%H:%M").time()
            if now >= scheduled_time: # Если время пришло
                logging.info(f"[Geyser Loop] Запускаю гейзер {event["id"]} в чате {event["chat_id"]} по расписанию {event["scheduled_time"]}")
                await send_geyser_event(bot, event["chat_id"], event["id"]) # Передаем event_id
        
        await asyncio.sleep(30) # Проверяем каждые 30 секунд

async def send_geyser_event(bot: Bot, chat_id: int, event_id: int): # Добавляем event_id
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=GEYSER_BUTTON_TEXT,
            callback_data=f"geyser_catch:{event_id}" # callback_data будет содержать event_id
        )
    )
    try:
        message = await bot.send_message(chat_id, GEYSER_MESSAGE, reply_markup=kb.as_markup())
        # Добавляем гейзер в активные, чтобы потом управлять таймаутом и обработкой
        timeout_task = asyncio.create_task(_geyser_timeout_task(bot, chat_id, message.message_id, event_id)) # Передаем event_id
        active_geysers[message.message_id] = { # Ключом будет message_id
            "chat_id": chat_id,
            "event_id": event_id, # Сохраняем event_id
            "sent_time": datetime.now(),
            "timeout_task": timeout_task, 
            "winner_id": None
        }
        logging.info(f"[Geyser] Гейзер отправлен в чат {chat_id}, сообщение {message.message_id}, event_id: {event_id}")

        # Обновляем статус в БД и message_id
        update_geyser_event_status(event_id, 'sent')
        update_geyser_event_message_id(event_id, message.message_id)

    except Exception as e:
        logging.error(f"[Geyser] Ошибка при отправке гейзера в чат {chat_id}: {e}")
