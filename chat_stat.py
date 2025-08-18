# chat_stats.py
import json
import os

STATS_FILE = "stats.json"


def _load_stats() -> dict:
    if not os.path.exists(STATS_FILE):
        return {}
    with open(STATS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _fmt_int(n: int) -> str:
    try:
        return f"{int(n):,}".replace(",", " ")
    except Exception:
        return str(n)


def _fmt_float(n: float, digits: int = 1) -> str:
    try:
        s = f"{float(n):.{digits}f}"
        # для вида "12 345.6" — только целую часть разделяем пробелами
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
    Возвращает компактную статистику чата:
    - за неделю (сумма по daily[0..6]) и за всё время (total) — в одной строке;
    - доп. метрики: флудонасыщенность (total chars/msg), средняя активность, активных участников, стикеров в час.
    """
    stats = _load_stats()
    cid = str(chat_id)
    if cid not in stats:
        return "Статистика для этого чата пока недоступна."

    weekly_messages = 0
    weekly_chars = 0
    weekly_stickers = 0

    total_messages = 0
    total_chars = 0
    total_stickers = 0

    users_seen_week = 0  # написали >=1 сообщение за последние 7 дней

    # Проходим по всем пользователям в чате
    for _, u in stats[cid].items():
        # --- total ---
        t = u.get("total", {})
        total_messages += int(t.get("messages", 0) or 0)
        total_chars += int(t.get("chars", 0) or 0)
        total_stickers += int(t.get("stickers", 0) or 0)

        # --- weekly (сумма по 7 ячейкам daily) ---
        week_msgs = 0
        week_chars = 0
        week_stk = 0
        for day in (u.get("daily") or [])[:7]:
            if not isinstance(day, dict):
                continue
            week_msgs += int(day.get("messages", 0) or 0)
            week_chars += int(day.get("chars", 0) or 0)
            week_stk += int(day.get("stickers", 0) or 0)

        if week_msgs > 0:
            users_seen_week += 1

        weekly_messages += week_msgs
        weekly_chars += week_chars
        weekly_stickers += week_stk

    # Средние длины сообщений
    avg_len_week = (weekly_chars / weekly_messages) if weekly_messages > 0 else 0.0
    avg_len_total = (total_chars / total_messages) if total_messages > 0 else 0.0

    # Стикеров в час за неделю
    stickers_per_hour = weekly_stickers / 168.0  # 7 * 24

    # Средняя активность на пользователя (за неделю)
    avg_activity_per_user = (weekly_messages / users_seen_week) if users_seen_week > 0 else 0.0

    # Флудонасыщенность — символов на сообщение (за всё время)
    flood_density = avg_len_total

    # Текст ответа
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
