import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message
from aiogram import Dispatcher

from db import get_connection, get_user_display_name
from dick import ensure_dicks_table


@dataclass
class RankingRow:
    position: int
    name: str
    value: int
    detail: str | None = None


def _get_last_days_messages(user_id: int, chat_id: int, days: int = 14) -> list[dict]:
    today = date.today()
    start_date = today - timedelta(days=days - 1)
    dates = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT date, messages
            FROM daily_stats
            WHERE user_id = ? AND chat_id = ? AND date BETWEEN ? AND ?
            """,
            (user_id, chat_id, start_date.isoformat(), today.isoformat()),
        )
        rows = cur.fetchall()
    rows_by_date = {row["date"]: int(row["messages"] or 0) for row in rows}
    return [{"date": d, "messages": rows_by_date.get(d, 0)} for d in dates]


def _get_reaction_totals(user_id: int, chat_id: int) -> tuple[int, int]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT react_taken, react_given
            FROM total_stats
            WHERE user_id = ? AND chat_id = ?
            """,
            (user_id, chat_id),
        )
        row = cur.fetchone()
    if not row:
        return 0, 0
    return int(row["react_taken"] or 0), int(row["react_given"] or 0)


def _get_ranking_rows(
    rows: Iterable[tuple[int, str, int, int | None]],
    user_id: int,
    default_name: str,
) -> tuple[List[RankingRow], int]:
    ordered = list(rows)
    if not ordered or all(user_id != row[0] for row in ordered):
        ordered.append((user_id, default_name, 0, 0))
    ordered.sort(key=lambda item: (-item[2], item[0]))

    user_position = 1
    ranking_rows: list[RankingRow] = []
    for idx, (row_user_id, name, value, extra) in enumerate(ordered, start=1):
        if row_user_id == user_id:
            user_position = idx
            break

    above_index = user_position - 2
    current_index = user_position - 1
    below_index = user_position

    for idx in [above_index, current_index, below_index]:
        if 0 <= idx < len(ordered):
            row_user_id, name, value, extra = ordered[idx]
            detail = f"{extra}" if extra is not None else None
            ranking_rows.append(RankingRow(idx + 1, name, value, detail))
    return ranking_rows, user_position


def _get_coffee_ranking(chat_id: int, user_id: int) -> tuple[List[RankingRow], int]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT u.user_id, u.name, COALESCE(t.coffee, 0) as coffee, NULL as extra
            FROM users u
            LEFT JOIN total_stats t ON t.user_id = u.user_id AND t.chat_id = u.chat_id
            WHERE u.chat_id = ?
            """,
            (chat_id,),
        )
        rows = cur.fetchall()
    default_name = get_user_display_name(user_id, chat_id)
    return _get_ranking_rows(rows, user_id, default_name)


def _get_message_ranking(chat_id: int, user_id: int) -> tuple[List[RankingRow], int]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT u.user_id,
                   u.name,
                   COALESCE(t.messages, 0) as messages,
                   CASE WHEN COALESCE(t.messages, 0) = 0 THEN 0
                        ELSE CAST(ROUND(COALESCE(t.chars, 0) * 1.0 / t.messages) AS INT)
                   END as avg_len
            FROM users u
            LEFT JOIN total_stats t ON t.user_id = u.user_id AND t.chat_id = u.chat_id
            WHERE u.chat_id = ?
            """,
            (chat_id,),
        )
        rows = cur.fetchall()
    default_name = get_user_display_name(user_id, chat_id)
    return _get_ranking_rows(rows, user_id, default_name)


def _get_dick_length(chat_id: int, user_id: int) -> int:
    ensure_dicks_table()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT length
            FROM dicks
            WHERE user_id = ? AND chat_id = ?
            """,
            (user_id, chat_id),
        )
        row = cur.fetchone()
    if not row:
        return 0
    return int(row["length"] or 0)


def _get_geyser_catch_count(chat_id: int, user_id: int) -> int:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) as total
            FROM geyser_events
            WHERE chat_id = ? AND status = 'caught' AND caught_by = ?
            """,
            (chat_id, user_id),
        )
        row = cur.fetchone()
    return int(row["total"] or 0)


def _get_activity_streak(chat_id: int, user_id: int) -> int:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT date
            FROM daily_stats
            WHERE chat_id = ? AND user_id = ? AND messages > 0
            ORDER BY date DESC
            """,
            (chat_id, user_id),
        )
        rows = cur.fetchall()

    if not rows:
        return 0

    streak = 0
    expected = date.today()
    row_dates = {datetime.strptime(row["date"], "%Y-%m-%d").date() for row in rows}
    while expected in row_dates:
        streak += 1
        expected -= timedelta(days=1)
    return streak


def _get_reaction_conversion(chat_id: int, user_id: int) -> float:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT react_taken, messages
            FROM total_stats
            WHERE chat_id = ? AND user_id = ?
            """,
            (chat_id, user_id),
        )
        row = cur.fetchone()
    if not row or not row["messages"]:
        return 0.0
    return float(row["react_taken"] or 0) / float(row["messages"] or 1)


def _get_peak_hours(chat_id: int, user_id: int) -> list[int]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        )
        if not cur.fetchone():
            return [0] * 24

        cur.execute("PRAGMA table_info(messages)")
        columns = [row["name"] for row in cur.fetchall()]
        date_column = None
        for candidate in ("date", "created_at", "timestamp", "sent_at"):
            if candidate in columns:
                date_column = candidate
                break
        if not date_column or "user_id" not in columns or "chat_id" not in columns:
            return [0] * 24

        try:
            cur.execute(
                f"""
                SELECT strftime('%H', {date_column}) as hour, COUNT(*) as total
                FROM messages
                WHERE chat_id = ? AND user_id = ?
                GROUP BY hour
                """,
                (chat_id, user_id),
            )
            rows = cur.fetchall()
        except Exception:
            return [0] * 24

    counts = [0] * 24
    for row in rows:
        if row["hour"] is None:
            continue
        hour = int(row["hour"])
        counts[hour] = int(row["total"] or 0)
    return counts


def _format_ranking_block(
    ax,
    title: str,
    rows: list[RankingRow],
    label_suffix: str,
    detail_label: str | None = None,
) -> None:
    ax.axis("off")
    ax.text(0.0, 1.0, title, fontsize=12, fontweight="bold", va="top")

    base_y = 0.78
    step = 0.22
    for idx, row in enumerate(rows):
        alpha = 1.0
        if idx in (0, 2) and len(rows) > 1:
            alpha = 0.45
        detail = f" ({detail_label}: {row.detail})" if detail_label and row.detail else ""
        line = f"{row.position}. {row.name} — {row.value} {label_suffix}{detail}"
        ax.text(0.02, base_y - idx * step, line, fontsize=11, alpha=alpha)


def _draw_dashboard(
    chat_id: int,
    user_id: int,
    user_name: str,
) -> bytes:
    flood_stats = _get_last_days_messages(user_id, chat_id)
    react_taken, react_given = _get_reaction_totals(user_id, chat_id)
    coffee_rows, coffee_pos = _get_coffee_ranking(chat_id, user_id)
    message_rows, message_pos = _get_message_ranking(chat_id, user_id)
    dick_length = _get_dick_length(chat_id, user_id)
    geyser_catches = _get_geyser_catch_count(chat_id, user_id)
    streak_days = _get_activity_streak(chat_id, user_id)
    reaction_conversion = _get_reaction_conversion(chat_id, user_id)
    peak_hours = _get_peak_hours(chat_id, user_id)

    fig = plt.figure(figsize=(12, 9.6), dpi=120)
    fig.patch.set_facecolor("#f7f5f2")
    grid = fig.add_gridspec(4, 2, height_ratios=[1.2, 1, 1, 1.1], hspace=0.5, wspace=0.3)

    ax_flood = fig.add_subplot(grid[0, 0])
    ax_react = fig.add_subplot(grid[0, 1])
    ax_coffee = fig.add_subplot(grid[1, :])
    ax_messages = fig.add_subplot(grid[2, 0])
    ax_extra = fig.add_subplot(grid[2, 1])
    ax_peak = fig.add_subplot(grid[3, :])

    fig.suptitle(f"Дашборд {user_name}", fontsize=16, fontweight="bold")

    dates = [item["date"] for item in flood_stats]
    messages = [item["messages"] for item in flood_stats]
    labels = [d[5:] for d in dates]
    ax_flood.plot(labels, messages, color="#9c6ade", linewidth=2)
    ax_flood.fill_between(labels, messages, color="#d4b8ff", alpha=0.5)
    ax_flood.set_title("Флуд за 2 недели (сообщения по дням)", fontsize=11)
    ax_flood.tick_params(axis="x", labelrotation=45, labelsize=8)
    ax_flood.tick_params(axis="y", labelsize=8)
    ax_flood.grid(alpha=0.2)

    ax_react.set_title("Реакции за всё время", fontsize=11)
    if react_taken + react_given == 0:
        ax_react.text(0.5, 0.5, "Нет реакций", ha="center", va="center", fontsize=12)
        ax_react.axis("off")
    else:
        ax_react.pie(
            [react_taken, react_given],
            labels=["Получено", "Поставлено"],
            autopct="%1.0f%%",
            colors=["#ff8fab", "#ffd6a5"],
            textprops={"fontsize": 9},
        )
        ax_react.axis("equal")

    _format_ranking_block(
        ax_coffee,
        f"Кофейный зачёт (место {coffee_pos})",
        coffee_rows,
        "☕",
    )

    _format_ranking_block(
        ax_messages,
        f"Сообщения (место {message_pos})",
        message_rows,
        "сообщ.",
        detail_label="ср. длина",
    )

    ax_extra.axis("off")
    ax_extra.text(0.0, 1.0, "Особые метрики", fontsize=12, fontweight="bold", va="top")
    ax_extra.text(0.02, 0.76, f"🔥 Дней подряд: {streak_days}", fontsize=11)
    ax_extra.text(0.02, 0.58, f"💞 Реакций на сообщение: {reaction_conversion:.2f}", fontsize=11)
    ax_extra.text(0.02, 0.4, f"🍆 Длина члена: {dick_length} см", fontsize=11)
    ax_extra.text(0.02, 0.22, f"⛲ Пойманные гейзеры: {geyser_catches}", fontsize=11)

    ax_peak.set_title("Пиковые часы активности", fontsize=11)
    hours = list(range(24))
    ax_peak.bar(hours, peak_hours, color="#90dbf4")
    ax_peak.set_xticks(hours)
    ax_peak.set_xticklabels([str(h) for h in hours], fontsize=7)
    ax_peak.tick_params(axis="y", labelsize=8)
    ax_peak.set_xlabel("Час", fontsize=9)
    ax_peak.set_ylabel("Сообщений", fontsize=9)
    ax_peak.grid(axis="y", alpha=0.2)

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)
    return buffer.read()


def register_dashboard_handlers(dp: Dispatcher) -> None:
    @dp.message(Command("dash"))
    async def dash_command(message: Message):
        user = message.from_user
        if not user:
            await message.answer("Не могу определить пользователя для дашборда.")
            return
        chat_id = message.chat.id
        user_id = user.id
        user_name = user.full_name or str(user_id)
        image_bytes = _draw_dashboard(chat_id, user_id, user_name)
        file = BufferedInputFile(image_bytes, filename="dashboard.png")
        await message.answer_photo(file)
