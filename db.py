import logging
import sqlite3
from contextlib import closing
from typing import List, Dict, Optional
from datetime import date, timedelta, datetime
import os
from sits import to_sits

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "stats.db")

def get_connection():
    """Создаёт подключение к БД"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row  # строки будут как словари
    return conn

def initialize_db():
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        # Таблица для отслеживания гейзеров (обновленная структура)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS geyser_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                scheduled_time TEXT NOT NULL, -- Время, когда гейзер должен появиться (ЧЧ:ММ)
                status TEXT DEFAULT 'pending', -- pending, sent, caught, expired
                message_id INTEGER, -- ID сообщения, которое отправит бот
                caught_by INTEGER, -- user_id поймавшего гейзер
                UNIQUE(chat_id, date, scheduled_time)
            )
        """)
        cursor.execute("PRAGMA table_info(geyser_events)")
        geyser_columns = {row["name"] for row in cursor.fetchall()}
        if "caught_by" not in geyser_columns:
            cursor.execute("ALTER TABLE geyser_events ADD COLUMN caught_by INTEGER")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sit_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                amount REAL NOT NULL
            )
        """)
        # Совместимость со статистикой укусов в sosalsa/weekly_awards.
        # В старых БД этих колонок может не быть.
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daily_stats'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(daily_stats)")
            daily_stats_columns = {row["name"] for row in cursor.fetchall()}
            if "bites_given" not in daily_stats_columns:
                cursor.execute("ALTER TABLE daily_stats ADD COLUMN bites_given INTEGER DEFAULT 0")
            if "bites_received" not in daily_stats_columns:
                cursor.execute("ALTER TABLE daily_stats ADD COLUMN bites_received INTEGER DEFAULT 0")

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='total_stats'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(total_stats)")
            total_stats_columns = {row["name"] for row in cursor.fetchall()}
            if "bites_given" not in total_stats_columns:
                cursor.execute("ALTER TABLE total_stats ADD COLUMN bites_given INTEGER DEFAULT 0")
            if "bites_received" not in total_stats_columns:
                cursor.execute("ALTER TABLE total_stats ADD COLUMN bites_received INTEGER DEFAULT 0")

        # Базовые ачивки укусов (если таблица achievements существует).
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='achievements'")
        if cursor.fetchone():
            cursor.execute("""
                INSERT OR IGNORE INTO achievements (key, name_m, name_f)
                VALUES ('biter', 'Кусака', 'Кусака')
            """)
            cursor.execute("""
                INSERT OR IGNORE INTO achievements (key, name_m, name_f)
                VALUES ('bitten', 'Месиво', 'Месиво')
            """)
        conn.commit()

# -------------------------------
# Работа с пользователями
# -------------------------------

def get_user(user_id: int, chat_id: int) -> Optional[sqlite3.Row]:
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        return cur.fetchone()




def get_chat_users(chat_id: int) -> List[sqlite3.Row]:
    """Возвращает всех пользователей чата."""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,))
        return cur.fetchall()

def get_all_chats(include_private: bool = False) -> list[int]:
    """Возвращает список всех chat_id, в которых есть пользователи"""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        if include_private:
            cur.execute("SELECT DISTINCT chat_id FROM users")
        else:
            cur.execute("SELECT DISTINCT chat_id FROM users WHERE chat_id < 0")
        return [row[0] for row in cur.fetchall()]



from typing import Optional
from contextlib import closing
from db import get_connection

def add_or_update_user(
    user_id: int,
    chat_id: int,
    name: Optional[str] = None,
    sits: Optional[float] = None,
    punished: Optional[int] = None,
    sex: Optional[str] = None,
    nick: Optional[str] = None,
    is_all: Optional[int] = None
):
    """Добавляет или обновляет пользователя. Меняем только те поля, что не None."""
    sits_value = None if sits is None else to_sits(sits)

    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (user_id, chat_id, name, sits, punished, sex, nick, is_all)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                name = COALESCE(excluded.name, users.name),
                sits = COALESCE(excluded.sits, users.sits),
                punished = COALESCE(excluded.punished, users.punished),
                sex = COALESCE(excluded.sex, users.sex),
                nick = COALESCE(excluded.nick, users.nick),
                is_all = COALESCE(excluded.is_all, users.is_all)
        """, (
            user_id,
            chat_id,
            name,
            sits_value,
            punished,
            sex,
            nick,
            is_all
        ))
        conn.commit()




def add_or_update_user_achievement(user_id: int, chat_id: int, achievement_key: str):
    """
    Добавляет запись о полученной пользователем ачивке в таблицу user_achievements.
    Если такая ачивка уже есть — игнорируем.
    """
    from contextlib import closing

    with closing(get_connection()) as conn:
        cur = conn.cursor()
        # Создаём запись, если её ещё нет
        cur.execute("""
            INSERT OR IGNORE INTO user_achievements
            (user_id, chat_id, achievement_key, date)
            VALUES (?, ?, ?, DATE('now'))
        """, (user_id, chat_id, achievement_key))
        conn.commit()


def update_user_sex(user_id: int, chat_id: int, sex: str):
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("UPDATE users SET sex=? WHERE user_id=? AND chat_id=?", (sex, user_id, chat_id))
        conn.commit()


def get_user_sex(user_id: int, chat_id: int) -> Optional[str]:
    """Возвращает пол пользователя: 'm', 'f' или None"""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT sex FROM users WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        row = cur.fetchone()
        return row["sex"] if row else None

def get_achievement_title(achievement_key: str, sex: str) -> str:
    """
    Возвращает название ачивки из таблицы achievements с учётом пола.
    sex: 'm', 'f' или None/другое.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT name_m, name_f FROM achievements WHERE key = ?
        """, (achievement_key,))
        row = cur.fetchone()

    if not row:
        return achievement_key  # fallback: если нет записи в БД

    name_m, name_f = row

    if sex == "m":
        return name_m
    elif sex == "f":
        return name_f
    else:
        # если неизвестно, по умолчанию мужская форма
        return name_m


# -------------------------------
# Работа с daily_stats
# -------------------------------

def add_or_update_daily_stats(user_id: int, chat_id: int, date_str: str,
                              messages=0, words=0, chars=0, stickers=0, coffee=0, profanity_count=0):
    """Добавляет или обновляет статистику за день"""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO daily_stats (user_id, chat_id, date, messages, words, chars, stickers, coffee, profanity_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id, date) DO UPDATE SET
                messages = excluded.messages,
                words = excluded.words,
                chars = excluded.chars,
                stickers = excluded.stickers,
                coffee = excluded.coffee,
                profanity_count = excluded.profanity_count
        """, (user_id, chat_id, date_str, messages, words, chars, stickers, coffee, profanity_count))
        conn.commit()


def increment_daily_stats(user_id: int, chat_id: int, date_str: str,
                          messages=0, words=0, chars=0, stickers=0, coffee=0, rounds=0, profanity_count=0):
    """Добавляет значения к дневной статистике или создаёт новую запись"""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO daily_stats (user_id, chat_id, date, messages, words, chars, stickers, coffee, rounds, profanity_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id, date) DO UPDATE SET
                messages = daily_stats.messages + excluded.messages,
                words = daily_stats.words + excluded.words,
                chars = daily_stats.chars + excluded.chars,
                stickers = daily_stats.stickers + excluded.stickers,
                coffee = daily_stats.coffee + excluded.coffee,
                rounds = daily_stats.rounds + excluded.rounds,
                profanity_count = daily_stats.profanity_count + excluded.profanity_count
        """, (user_id, chat_id, date_str, messages, words, chars, stickers, coffee, rounds, profanity_count))
        conn.commit()


def get_daily_stats(user_id: int, chat_id: int, date_str: str) -> Optional[sqlite3.Row]:
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM daily_stats WHERE user_id=? AND chat_id=? AND date=?", (user_id, chat_id, date_str))
        return cur.fetchone()


def get_last_7_daily_stats(user_id: int, chat_id: int, days: int = 7) -> list[dict]:
    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(days)]
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT date, messages, words, chars, stickers, coffee
            FROM daily_stats
            WHERE user_id=? AND chat_id=? AND date BETWEEN ? AND ?
        """, (user_id, chat_id, dates[-1], dates[0]))
        rows = cur.fetchall()
    rows_by_date = {row["date"]: row for row in rows}
    result = []
    for d in dates:
        if d in rows_by_date:
            r = rows_by_date[d]
            result.append({k: int(r[k] or 0) for k in ["messages", "words", "chars", "stickers", "coffee"]} | {"date": d})
        else:
            result.append({"date": d, "messages": 0, "words": 0, "chars": 0, "stickers": 0, "coffee": 0})
    return result


# -------------------------------
# Работа с total_stats
# -------------------------------

def add_or_update_total_stats(user_id: int, chat_id: int,
                              messages=0, words=0, chars=0, stickers=0, coffee=0, profanity_count=0):
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO total_stats (user_id, chat_id, messages, words, chars, stickers, coffee, profanity_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                messages = excluded.messages,
                words = excluded.words,
                chars = excluded.chars,
                stickers = excluded.stickers,
                coffee = excluded.coffee,
                profanity_count = excluded.profanity_count
        """, (user_id, chat_id, messages, words, chars, stickers, coffee, profanity_count))
        conn.commit()


def increment_total_stats(user_id: int, chat_id: int,
                          messages=0, words=0, chars=0, stickers=0, coffee=0, rounds=0, profanity_count=0):
    """Добавляет значения к общей статистике пользователя или создаёт новую запись."""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO total_stats (user_id, chat_id, messages, words, chars, stickers, coffee, rounds, profanity_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                messages = total_stats.messages + excluded.messages,
                words = total_stats.words + excluded.words,
                chars = total_stats.chars + excluded.chars,
                stickers = total_stats.stickers + excluded.stickers,
                coffee = total_stats.coffee + excluded.coffee,
                rounds = total_stats.rounds + excluded.rounds,
                profanity_count = total_stats.profanity_count + excluded.profanity_count
        """, (user_id, chat_id, messages, words, chars, stickers, coffee, rounds, profanity_count))
        conn.commit()

def increment_sticker_stats(chat_id: int, file_id: str, set_name: str | None = None, date_str: str | None = None):
    """
    Увеличивает счётчик для (chat_id, file_id, date).
    date_str: 'YYYY-MM-DD'. Если None — берётся сегодня.
    """
    if date_str is None:
        date_str = date.today().isoformat()

    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO sticker_stats (chat_id, file_id, set_name, date, count)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(chat_id, file_id, date) DO UPDATE SET
                count = sticker_stats.count + 1,
                set_name = COALESCE(excluded.set_name, sticker_stats.set_name)
        """, (chat_id, file_id, set_name, date_str))
        conn.commit()

def get_total_stats(user_id: int, chat_id: int) -> Optional[sqlite3.Row]:
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM total_stats WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        return cur.fetchone()


def get_user_display_name(user_id: int, chat_id: int) -> str:
    """Возвращает имя пользователя с префиксом 👑 при активной подписке."""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT name, subscription_till
                FROM users
                WHERE user_id = ? AND chat_id = ?
                """,
                (user_id, chat_id),
            )
            row = cur.fetchone()
        except sqlite3.OperationalError:
            cur.execute(
                """
                SELECT name
                FROM users
                WHERE user_id = ? AND chat_id = ?
                """,
                (user_id, chat_id),
            )
            row = cur.fetchone()
            subscription_till = ""
        else:
            subscription_till = row["subscription_till"] if row else ""

    base_name = (row["name"] if row and row["name"] else str(user_id))
    if has_active_subscription_str(subscription_till):
        return base_name if base_name.startswith("👑 ") else f"👑 {base_name}"
    return base_name


def has_active_subscription_str(subscription_till: str | None) -> bool:
    if not subscription_till:
        return False
    try:
        till_date = datetime.strptime(subscription_till, "%Y-%m-%d").date()
    except ValueError:
        return False
    return till_date >= date.today()


def has_active_subscription(chat_id: int, user_id: int) -> bool:
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT subscription_till
                FROM users
                WHERE user_id = ? AND chat_id = ?
                """,
                (user_id, chat_id),
            )
            row = cur.fetchone()
        except sqlite3.OperationalError:
            return False
    return has_active_subscription_str(row["subscription_till"] if row else "")

def add_sits(chat_id: int, user_id: int, amount: float):
    """Добавляет или вычитает сит для пользователя."""
    # Убеждаемся, что пользователь существует
    delta = to_sits(amount)
    if delta == 0:
        return

    user = get_user(user_id, chat_id)
    if user is None:
        # Если пользователя нет, создаем его с указанным количеством сит
        add_or_update_user(user_id, chat_id, name="", sits=delta)
    else:
        # Если пользователь есть, обновляем его количество сит
        new_sits = to_sits((user["sits"] or 0) + delta)
        add_or_update_user(user_id, chat_id, name=user["name"], sits=new_sits)
    if delta > 0:
        now = datetime.now()
        name = get_user_display_name(user_id, chat_id)
        with closing(get_connection()) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO sit_stats (date, time, chat_id, user_id, name, amount)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (now.date().isoformat(), now.strftime("%H:%M:%S"), chat_id, user_id, name, delta))
            conn.commit()

# --- Функции для работы с гейзером ---
def add_geyser_event(chat_id: int, date_str: str, scheduled_time: str, status: str = 'pending'):
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO geyser_events (chat_id, date, scheduled_time, status)
            VALUES (?, ?, ?, ?)
        """, (chat_id, date_str, scheduled_time, status))
        conn.commit()

def get_pending_geyser_events(date_str: str) -> List[sqlite3.Row]:
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM geyser_events WHERE date=? AND status='pending'", (date_str,))
        return cur.fetchall()

def update_geyser_event_status(event_id: int, new_status: str):
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("UPDATE geyser_events SET status=? WHERE id=?", (new_status, event_id))
        conn.commit()

def update_geyser_event_message_id(event_id: int, message_id: int):
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("UPDATE geyser_events SET message_id=? WHERE id=?", (message_id, event_id))
        conn.commit()

def update_geyser_event_caught_by(event_id: int, user_id: int):
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("UPDATE geyser_events SET caught_by=? WHERE id=?", (user_id, event_id))
        conn.commit()

# Вызываем инициализацию при загрузке модуля
initialize_db()
