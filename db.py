import sqlite3
from contextlib import closing
from typing import List, Dict, Optional

DB_FILE = "stats.db"


def get_connection():
    """Создаёт подключение к БД"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row  # строки будут как словари
    return conn


# -------------------------------
# Работа с пользователями
# -------------------------------

def get_user(user_id):
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return cur.fetchone()

def get_chat_users(chat_id: int) -> list[sqlite3.Row]:
    """Возвращает всех пользователей чата."""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,))
        return cur.fetchall()



def add_or_update_user(user_id, chat_id, name, sits=0, punished=0, sex=None):
    """Добавляет или обновляет пользователя"""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (user_id, chat_id, name, sits, punished, sex)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                name = excluded.name,
                sits = excluded.sits,
                punished = excluded.punished,
                sex = COALESCE(users.sex, excluded.sex) -- не затираем старый sex
        """, (user_id, chat_id, name, sits, punished, sex))
        conn.commit()


def update_user_sex(user_id, sex):
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("UPDATE users SET sex = ? WHERE user_id = ?", (sex, user_id))
        conn.commit()

def get_user_sex(user_id: int, chat_id: int) -> str | None:
    """
    Возвращает пол пользователя: 'm', 'f' или None, если не указан.
    """
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute("SELECT sex FROM users WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        row = cur.fetchone()
        if row:
            return row[0]  # 'm', 'f' или None
        return None
    finally:
        conn.close()

# -------------------------------
# Работа с daily_stats
# -------------------------------

def add_or_update_daily_stats(user_id, chat_id, date, messages=0, words=0, chars=0, stickers=0, coffee=0):
    """Добавляет или обновляет статистику за день"""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO daily_stats (user_id, chat_id, date, messages, words, chars, stickers, coffee)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, date) DO UPDATE SET
                messages = excluded.messages,
                words = excluded.words,
                chars = excluded.chars,
                stickers = excluded.stickers,
                coffee = excluded.coffee
        """, (user_id, chat_id, date, messages, words, chars, stickers, coffee))
        conn.commit()


def get_daily_stats(user_id, date):
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM daily_stats WHERE user_id = ? AND date = ?", (user_id, date))
        return cur.fetchone()


# -------------------------------
# Работа с total_stats
# -------------------------------

def add_or_update_total_stats(user_id, chat_id, messages=0, words=0, chars=0, stickers=0, coffee=0):
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO total_stats (user_id, chat_id, messages, words, chars, stickers, coffee)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                messages = excluded.messages,
                words = excluded.words,
                chars = excluded.chars,
                stickers = excluded.stickers,
                coffee = excluded.coffee
        """, (user_id, chat_id, messages, words, chars, stickers, coffee))
        conn.commit()


def get_total_stats(user_id):
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM total_stats WHERE user_id = ?", (user_id,))
        return cur.fetchone()


def increment_daily_stats(user_id, chat_id, date, messages=0, words=0, chars=0, stickers=0, coffee=0):
    """Добавляет значения к дневной статистике или создаёт новую запись."""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO daily_stats (user_id, chat_id, date, messages, words, chars, stickers, coffee)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, date) DO UPDATE SET
                messages = daily_stats.messages + excluded.messages,
                words = daily_stats.words + excluded.words,
                chars = daily_stats.chars + excluded.chars,
                stickers = daily_stats.stickers + excluded.stickers,
                coffee = daily_stats.coffee + excluded.coffee
        """, (user_id, chat_id, date, messages, words, chars, stickers, coffee))
        conn.commit()


def increment_total_stats(user_id, chat_id, messages=0, words=0, chars=0, stickers=0, coffee=0):
    """Добавляет значения к общей статистике пользователя или создаёт новую запись."""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO total_stats (user_id, chat_id, messages, words, chars, stickers, coffee)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                messages = total_stats.messages + excluded.messages,
                words = total_stats.words + excluded.words,
                chars = total_stats.chars + excluded.chars,
                stickers = total_stats.stickers + excluded.stickers,
                coffee = total_stats.coffee + excluded.coffee
        """, (user_id, chat_id, messages, words, chars, stickers, coffee))
        conn.commit()
