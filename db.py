import json
import logging
import sqlite3
from contextlib import closing
from typing import List, Dict, Optional
from datetime import date, timedelta, datetime
import os
from pathlib import Path
from sits import to_sits

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "stats.db")
WEB_CHAT_MEDIA_DIR = os.path.join(BASE_DIR, "web_chat_media")
WEB_CHAT_MEDIA_RETENTION_DAYS = 3
SQLITE_BUSY_TIMEOUT_MS = 5_000

def get_connection():
    """Create a consistently configured SQLite connection."""
    conn = sqlite3.connect(DB_FILE, timeout=SQLITE_BUSY_TIMEOUT_MS / 1_000)
    conn.row_factory = sqlite3.Row  # строки будут как словари
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA wal_autocheckpoint=1000")
    return conn


def ensure_web_chat_media_schema() -> None:
    os.makedirs(WEB_CHAT_MEDIA_DIR, exist_ok=True)
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS web_chat_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                attachment_index INTEGER NOT NULL DEFAULT 0,
                media_type TEXT NOT NULL,
                telegram_file_id TEXT NOT NULL,
                telegram_file_unique_id TEXT,
                local_path TEXT NOT NULL,
                mime_type TEXT DEFAULT 'image/jpeg',
                width INTEGER,
                height INTEGER,
                file_size INTEGER,
                created_at TEXT NOT NULL,
                UNIQUE(chat_id, message_id, attachment_index)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_chat_attachments_message
            ON web_chat_attachments(chat_id, message_id)
            """
        )
        conn.commit()


def _safe_unlink_web_chat_media(path_value: str) -> bool:
    if not path_value:
        return False
    media_root = Path(WEB_CHAT_MEDIA_DIR).resolve()
    try:
        file_path = Path(path_value).resolve()
        file_path.relative_to(media_root)
    except (OSError, ValueError):
        logging.warning("Skipping unsafe web chat media path: %s", path_value)
        return False
    if not file_path.is_file():
        return False
    try:
        file_path.unlink()
        return True
    except OSError:
        logging.exception("Failed to delete web chat media file: %s", file_path)
        return False


def cleanup_web_chat_media(retention_days: int = WEB_CHAT_MEDIA_RETENTION_DAYS) -> dict[str, int]:
    ensure_web_chat_media_schema()
    retention_days = max(1, int(retention_days or WEB_CHAT_MEDIA_RETENTION_DAYS))
    cutoff = datetime.now() - timedelta(days=retention_days)
    cutoff_iso = cutoff.isoformat()
    media_root = Path(WEB_CHAT_MEDIA_DIR).resolve()
    deleted_files = 0
    deleted_rows = 0
    deleted_orphans = 0

    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, local_path
            FROM web_chat_attachments
            WHERE created_at < ?
            """,
            (cutoff_iso,),
        )
        expired_rows = cursor.fetchall()
        for row in expired_rows:
            if _safe_unlink_web_chat_media(str(row["local_path"] or "")):
                deleted_files += 1
        if expired_rows:
            cursor.executemany(
                "DELETE FROM web_chat_attachments WHERE id = ?",
                [(int(row["id"]),) for row in expired_rows],
            )
            deleted_rows = len(expired_rows)

        cursor.execute("SELECT local_path FROM web_chat_attachments")
        referenced_paths = {
            str(Path(str(row["local_path"] or "")).resolve())
            for row in cursor.fetchall()
            if row["local_path"]
        }
        conn.commit()

    cutoff_ts = cutoff.timestamp()
    if media_root.is_dir():
        for file_path in media_root.rglob("*"):
            if not file_path.is_file():
                continue
            resolved = str(file_path.resolve())
            if resolved in referenced_paths:
                continue
            try:
                if file_path.stat().st_mtime >= cutoff_ts:
                    continue
                file_path.unlink()
                deleted_files += 1
                deleted_orphans += 1
            except OSError:
                logging.exception("Failed to delete orphan web chat media file: %s", file_path)

        for dir_path in sorted((p for p in media_root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                dir_path.rmdir()
            except OSError:
                pass

    return {
        "deleted_rows": deleted_rows,
        "deleted_files": deleted_files,
        "deleted_orphans": deleted_orphans,
    }

def initialize_db():
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(users)")
            user_columns = {row["name"] for row in cursor.fetchall()}
            if "nick" not in user_columns:
                cursor.execute("ALTER TABLE users ADD COLUMN nick TEXT")
            if "cepen" not in user_columns:
                cursor.execute("ALTER TABLE users ADD COLUMN cepen REAL NOT NULL DEFAULT 0")
            if "cepen_growth_date" not in user_columns:
                cursor.execute("ALTER TABLE users ADD COLUMN cepen_growth_date TEXT")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cepen_event_checks (
                event_kind TEXT NOT NULL,
                event_id TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                checked_at TEXT NOT NULL,
                PRIMARY KEY (event_kind, event_id, chat_id)
            )
        """)
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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sit_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                nick TEXT NOT NULL DEFAULT '',
                display_name TEXT NOT NULL DEFAULT '',
                amount REAL NOT NULL,
                balance_before REAL NOT NULL,
                balance_after REAL NOT NULL,
                action_code TEXT NOT NULL,
                action_ru TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sit_ledger_user_chat_created
            ON sit_ledger(user_id, chat_id, created_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sit_ledger_chat_created
            ON sit_ledger(chat_id, created_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sit_ledger_action_created
            ON sit_ledger(action_code, created_at)
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

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages_reactions'")
        if cursor.fetchone():
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_reactions_chat_date
                ON messages_reactions(chat_id, date)
                """
            )

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


def cepen_enabled(chat_id: int, conn: sqlite3.Connection | None = None) -> bool:
    """The tapeworm is enabled by default, including in existing chats."""
    if conn is None:
        with closing(get_connection()) as owned_conn:
            return cepen_enabled(chat_id, owned_conn)
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE chat_id=? AND name='enable_cepen'",
            (chat_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return True
    return row is None or bool(int(float(row[0])))



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


def record_message_activity(
    *,
    user_id: int,
    chat_id: int,
    user_name: str,
    nick: str | None,
    message_id: int,
    message_text: str,
    date_str: str,
    message_datetime: str,
    messages: int = 0,
    words: int = 0,
    chars: int = 0,
    stickers: int = 0,
    rounds: int = 0,
    profanity_count: int = 0,
    sticker_file_id: str | None = None,
    sticker_set_name: str | None = None,
) -> None:
    """Persist one Telegram message and its counters in a single transaction."""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                """
                INSERT INTO users (user_id, chat_id, name, nick)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, chat_id) DO UPDATE SET
                    name = excluded.name,
                    nick = COALESCE(excluded.nick, users.nick)
                WHERE users.name IS NOT excluded.name
                   OR (excluded.nick IS NOT NULL AND users.nick IS NOT excluded.nick)
                """,
                (user_id, chat_id, user_name, nick),
            )
            cur.execute(
                """
                INSERT INTO daily_stats
                    (user_id, chat_id, date, messages, words, chars, stickers, rounds, profanity_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, chat_id, date) DO UPDATE SET
                    messages = daily_stats.messages + excluded.messages,
                    words = daily_stats.words + excluded.words,
                    chars = daily_stats.chars + excluded.chars,
                    stickers = daily_stats.stickers + excluded.stickers,
                    rounds = daily_stats.rounds + excluded.rounds,
                    profanity_count = daily_stats.profanity_count + excluded.profanity_count
                """,
                (user_id, chat_id, date_str, messages, words, chars, stickers, rounds, profanity_count),
            )
            cur.execute(
                """
                INSERT INTO total_stats
                    (user_id, chat_id, messages, words, chars, stickers, rounds, profanity_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, chat_id) DO UPDATE SET
                    messages = total_stats.messages + excluded.messages,
                    words = total_stats.words + excluded.words,
                    chars = total_stats.chars + excluded.chars,
                    stickers = total_stats.stickers + excluded.stickers,
                    rounds = total_stats.rounds + excluded.rounds,
                    profanity_count = total_stats.profanity_count + excluded.profanity_count
                """,
                (user_id, chat_id, messages, words, chars, stickers, rounds, profanity_count),
            )
            cur.execute(
                """
                INSERT OR IGNORE INTO messages_reactions
                    (chat_id, message_id, user_id, message_text, reactions_count, date)
                VALUES (?, ?, ?, ?, 0, ?)
                """,
                (chat_id, message_id, user_id, message_text, message_datetime),
            )
            if sticker_file_id:
                cur.execute(
                    """
                    INSERT INTO sticker_stats (chat_id, file_id, set_name, date, count)
                    VALUES (?, ?, ?, ?, 1)
                    ON CONFLICT(chat_id, file_id, date) DO UPDATE SET
                        count = sticker_stats.count + 1,
                        set_name = COALESCE(excluded.set_name, sticker_stats.set_name)
                    """,
                    (chat_id, sticker_file_id, sticker_set_name, date_str),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

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


def get_user_display_name(user_id: int, chat_id: int, name_override: str | None = None) -> str:
    """Возвращает имя пользователя с игровыми префиксами."""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        show_cepen = cepen_enabled(chat_id, conn)
        try:
            cur.execute(
                """
                SELECT name, subscription_till, cepen
                FROM users
                WHERE user_id = ? AND chat_id = ?
                """,
                (user_id, chat_id),
            )
            row = cur.fetchone()
        except sqlite3.OperationalError:
            cur.execute(
                """
                SELECT name, cepen
                FROM users
                WHERE user_id = ? AND chat_id = ?
                """,
                (user_id, chat_id),
            )
            row = cur.fetchone()
            subscription_till = ""
            cepen_length = row["cepen"] if row else 0
        else:
            subscription_till = row["subscription_till"] if row else ""
            cepen_length = row["cepen"] if row else 0

    base_name = name_override or (row["name"] if row and row["name"] else str(user_id))
    prefixes = []
    if has_active_subscription_str(subscription_till):
        prefixes.append("👑")
    if show_cepen and float(cepen_length or 0) > 0:
        prefixes.append("🪱")
    for prefix in ("👑 ", "🪱 "):
        if base_name.startswith(prefix):
            base_name = base_name[len(prefix):]
    return " ".join([*prefixes, base_name])


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

class InsufficientSitsError(ValueError):
    def __init__(self, balance: float):
        super().__init__("insufficient sits")
        self.balance = to_sits(balance)


def apply_sit_change(
    conn: sqlite3.Connection,
    chat_id: int,
    user_id: int,
    amount: float,
    *,
    action_code: str,
    action_ru: str,
    metadata: Optional[dict] = None,
    require_sufficient: bool = False,
) -> tuple[float, float]:
    """Change a balance and append its audit row using the caller's transaction."""
    delta = to_sits(amount)
    if delta == 0:
        raise ValueError("sit change amount must not be zero")
    if not action_code.strip() or not action_ru.strip():
        raise ValueError("action_code and action_ru are required")

    cur = conn.cursor()
    cur.execute(
        """
        SELECT COALESCE(name, '') AS name,
               COALESCE(nick, '') AS nick,
               COALESCE(sits, 0) AS sits
        FROM users
        WHERE user_id = ? AND chat_id = ?
        """,
        (user_id, chat_id),
    )
    row = cur.fetchone()
    balance_before = to_sits(row["sits"] if row else 0)
    balance_after = to_sits(balance_before + delta)
    if require_sufficient and delta < 0 and balance_after < 0:
        raise InsufficientSitsError(balance_before)

    if row is None:
        cur.execute(
            """
            INSERT INTO users (user_id, chat_id, name, sits)
            VALUES (?, ?, '', ?)
            """,
            (user_id, chat_id, balance_after),
        )
        display_name = ""
        nick = ""
    else:
        cur.execute(
            """
            UPDATE users
            SET sits = ?
            WHERE user_id = ? AND chat_id = ?
            """,
            (balance_after, user_id, chat_id),
        )
        display_name = str(row["name"] or "")
        nick = str(row["nick"] or "")

    if nick and not nick.startswith("@"):
        nick = f"@{nick}"
    now = datetime.now()
    date_value = now.date().isoformat()
    time_value = now.strftime("%H:%M:%S")
    cur.execute(
        """
        INSERT INTO sit_ledger (
            created_at, date, time, chat_id, user_id, nick, display_name,
            amount, balance_before, balance_after, action_code, action_ru,
            metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now.isoformat(timespec="seconds"),
            date_value,
            time_value,
            chat_id,
            user_id,
            nick,
            display_name,
            delta,
            balance_before,
            balance_after,
            action_code.strip(),
            action_ru.strip(),
            json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
        ),
    )

    # Keep the legacy income table intact for existing reports.
    if delta > 0:
        cur.execute(
            """
            INSERT INTO sit_stats (date, time, chat_id, user_id, name, amount)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (date_value, time_value, chat_id, user_id, display_name or nick, delta),
        )
    return balance_before, balance_after


def change_sits(
    chat_id: int,
    user_id: int,
    amount: float,
    *,
    action_code: str,
    action_ru: str,
    metadata: Optional[dict] = None,
    require_sufficient: bool = False,
) -> tuple[float, float]:
    """Atomically change a user's balance and record the movement."""
    with closing(get_connection()) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = apply_sit_change(
                conn,
                chat_id,
                user_id,
                amount,
                action_code=action_code,
                action_ru=action_ru,
                metadata=metadata,
                require_sufficient=require_sufficient,
            )
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise


def add_sits(
    chat_id: int,
    user_id: int,
    amount: float,
    *,
    action_code: str,
    action_ru: str,
    metadata: Optional[dict] = None,
) -> float:
    """Return the new balance after an audited change."""
    _, balance_after = change_sits(
        chat_id,
        user_id,
        amount,
        action_code=action_code,
        action_ru=action_ru,
        metadata=metadata,
    )
    return balance_after

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


def expire_geyser_event_if_sent(event_id: int) -> bool:
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE geyser_events SET status = 'expired' WHERE id = ? AND status = 'sent'",
            (event_id,),
        )
        conn.commit()
        return cur.rowcount == 1


def get_geyser_event(event_id: int) -> Optional[sqlite3.Row]:
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, chat_id, date, scheduled_time, status, message_id, caught_by
            FROM geyser_events
            WHERE id = ?
            """,
            (event_id,),
        )
        return cur.fetchone()


def claim_geyser_event_with_reward(
    event_id: int,
    chat_id: int,
    message_id: int,
    user_id: int,
    reward: float,
) -> bool:
    """Atomically claim a sent geyser and apply its non-zero reward."""
    with closing(get_connection()) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE geyser_events
                SET status = 'caught', caught_by = ?
                WHERE id = ?
                  AND chat_id = ?
                  AND message_id = ?
                  AND status = 'sent'
                """,
                (user_id, event_id, chat_id, message_id),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return False

            if to_sits(reward) != 0:
                apply_sit_change(
                    conn,
                    chat_id,
                    user_id,
                    reward,
                    action_code="geyser_catch_reward",
                    action_ru="Награда за поимку гейзера",
                    metadata={"event_id": event_id, "message_id": message_id},
                )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise

# Тестовый раннер отключает побочный эффект импорта и создаёт отдельные БД в setUp.
if os.getenv("UDB_SKIP_DB_INIT") != "1":
    initialize_db()
