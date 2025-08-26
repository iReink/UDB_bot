# chat_stats.py
import sqlite3
from datetime import datetime, timedelta

DB_FILE = "stats.db"

def _fmt_int(n: int) -> str:
    try:
        return f"{int(n):,}".replace(",", " ")
    except Exception:
        return str(n)

def _fmt_float(n: float, digits: int = 1) -> str:
    try:
        s = f"{float(n):.{digits}f}"
        if "." in s:
            whole, frac = s.split(".")
            whole_fmt = f"{int(whole):,}".replace(",", " ")
            return f"{whole_fmt}.{frac}"
        else:
            return f"{int(float(s)):,}".replace(",", " ")
    except Exception:
        return str(n)

def get_weekly_chat_stats(chat_id: int) -> str:
    """
    Возвращает статистику чата по последней неделе и за всё время из SQLite.
    """
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    today = datetime.now().date()
    week_start = today - timedelta(days=6)  # последние 7 дней включая сегодня

    # --- weekly ---
    cur.execute("""
        SELECT SUM(messages), SUM(chars), SUM(stickers), COUNT(DISTINCT user_id)
        FROM daily_stats
        WHERE chat_id = ? AND date BETWEEN ? AND ?
    """, (chat_id, week_start.isoformat(), today.isoformat()))
    row = cur.fetchone()
    weekly_messages, weekly_chars, weekly_stickers, users_seen_week = row if row else (0,0,0,0)

    # --- total ---
    cur.execute("""
        SELECT SUM(messages), SUM(chars), SUM(stickers)
        FROM total_stats
        WHERE chat_id = ?
    """, (chat_id,))
    row = cur.fetchone()
    total_messages, total_chars, total_stickers = row if row else (0,0,0)

    conn.close()

    avg_len_week = (weekly_chars / weekly_messages) if weekly_messages > 0 else 0.0
    avg_len_total = (total_chars / total_messages) if total_messages > 0 else 0.0

    stickers_per_hour = weekly_stickers / 168.0  # 7*24
    avg_activity_per_user = (weekly_messages / users_seen_week) if users_seen_week > 0 else 0.0
    flood_density = avg_len_total

    lines = []
    lines.append("📊 Статистика чата за неделю (за всё время)")
    lines.append(f"- Сообщений: {_fmt_int(weekly_messages)} ({_fmt_int(total_messages)})")
    lines.append(f"- Стикеров: {_fmt_int(weekly_stickers)} ({_fmt_int(total_stickers)})")
    lines.append(f"- Средняя длина сообщения: {_fmt_float(avg_len_week, 1)} символов ({_fmt_float(avg_len_total, 1)})")
    lines.append("")
    lines.append("Дополнительно:")
    lines.append(f"- Средняя активность: {_fmt_float(avg_activity_per_user, 1)} сообщений на пользователя за неделю")
    lines.append(f"- Активных участников: {_fmt_int(users_seen_week)}")
    lines.append(f"- Стикеров в час: {_fmt_float(stickers_per_hour, 2)}")

    return "\n".join(lines)
