import sqlite3
from contextlib import closing
from typing import List, Dict, Optional
from datetime import date, timedelta

DB_FILE = "stats.db"


def get_connection():
    """Создаёт подключение к БД"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row  # строки будут как словари
    return conn


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

def get_all_chats() -> list[int]:
    """Возвращает список всех chat_id, в которых есть пользователи"""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT chat_id FROM users")
        return [row[0] for row in cur.fetchall()]



def add_or_update_user(user_id: int, chat_id: int, name: str,
                       sits: Optional[int] = None,
                       punished: Optional[int] = None,
                       sex: Optional[str] = None):
    """Добавляет или обновляет пользователя. Если sit/punished/sex = None, не меняем."""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (user_id, chat_id, name, sits, punished, sex)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                name = excluded.name,
                sits = COALESCE(excluded.sits, users.sits),
                punished = COALESCE(excluded.punished, users.punished),
                sex = COALESCE(users.sex, excluded.sex)
        """, (user_id, chat_id, name, sits, punished, sex))
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


# -------------------------------
# Работа с daily_stats
# -------------------------------

def add_or_update_daily_stats(user_id: int, chat_id: int, date_str: str,
                              messages=0, words=0, chars=0, stickers=0, coffee=0):
    """Добавляет или обновляет статистику за день"""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO daily_stats (user_id, chat_id, date, messages, words, chars, stickers, coffee)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id, date) DO UPDATE SET
                messages = excluded.messages,
                words = excluded.words,
                chars = excluded.chars,
                stickers = excluded.stickers,
                coffee = excluded.coffee
        """, (user_id, chat_id, date_str, messages, words, chars, stickers, coffee))
        conn.commit()


def increment_daily_stats(user_id: int, chat_id: int, date_str: str,
                          messages=0, words=0, chars=0, stickers=0, coffee=0):
    """Добавляет значения к дневной статистике или создаёт новую запись"""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO daily_stats (user_id, chat_id, date, messages, words, chars, stickers, coffee)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id, date) DO UPDATE SET
                messages = daily_stats.messages + excluded.messages,
                words = daily_stats.words + excluded.words,
                chars = daily_stats.chars + excluded.chars,
                stickers = daily_stats.stickers + excluded.stickers,
                coffee = daily_stats.coffee + excluded.coffee
        """, (user_id, chat_id, date_str, messages, words, chars, stickers, coffee))
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
                              messages=0, words=0, chars=0, stickers=0, coffee=0):
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO total_stats (user_id, chat_id, messages, words, chars, stickers, coffee)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                messages = excluded.messages,
                words = excluded.words,
                chars = excluded.chars,
                stickers = excluded.stickers,
                coffee = excluded.coffee
        """, (user_id, chat_id, messages, words, chars, stickers, coffee))
        conn.commit()


def increment_total_stats(user_id: int, chat_id: int,
                          messages=0, words=0, chars=0, stickers=0, coffee=0):
    """Добавляет значения к общей статистике пользователя или создаёт новую запись."""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO total_stats (user_id, chat_id, messages, words, chars, stickers, coffee)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                messages = total_stats.messages + excluded.messages,
                words = total_stats.words + excluded.words,
                chars = total_stats.chars + excluded.chars,
                stickers = total_stats.stickers + excluded.stickers,
                coffee = total_stats.coffee + excluded.coffee
        """, (user_id, chat_id, messages, words, chars, stickers, coffee))
        conn.commit()


def get_total_stats(user_id: int, chat_id: int) -> Optional[sqlite3.Row]:
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM total_stats WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        return cur.fetchone()
