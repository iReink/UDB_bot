import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message
from aiogram import Dispatcher

from db import get_connection, get_user_display_name
from dick import ensure_dicks_table, get_dick, get_dick_rankings


@dataclass
class RankingRow:
    position: int
    name: str
    value: float
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
    rows: Iterable[tuple[int, str, float, int | None]],
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
                   NULL as extra
            FROM users u
            LEFT JOIN total_stats t ON t.user_id = u.user_id AND t.chat_id = u.chat_id
            WHERE u.chat_id = ?
            """,
            (chat_id,),
        )
        rows = cur.fetchall()
    default_name = get_user_display_name(user_id, chat_id)
    return _get_ranking_rows(rows, user_id, default_name)


def _get_avg_message_length_ranking(
    chat_id: int, user_id: int
) -> tuple[List[RankingRow], int]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT u.user_id,
                   u.name,
                   CASE WHEN COALESCE(t.messages, 0) = 0 THEN 0
                        ELSE CAST(ROUND(COALESCE(t.chars, 0) * 1.0 / t.messages) AS INT)
                   END as avg_len,
                   NULL as extra
            FROM users u
            LEFT JOIN total_stats t ON t.user_id = u.user_id AND t.chat_id = u.chat_id
            WHERE u.chat_id = ?
            """,
            (chat_id,),
        )
        rows = cur.fetchall()
    default_name = get_user_display_name(user_id, chat_id)
    return _get_ranking_rows(rows, user_id, default_name)


def _get_dick_length_ranking(
    chat_id: int, user_id: int
) -> tuple[List[RankingRow], int | None]:
    ensure_dicks_table()
    rankings = get_dick_rankings(chat_id, only_grown=True)
    ordered = [
        (row["user_id"], get_user_display_name(row["user_id"], chat_id), int(row["length"] or 0), None)
        for row in rankings
    ]

    for idx, (row_user_id, _, _, _) in enumerate(ordered, start=1):
        if row_user_id == user_id:
            ranking_rows: list[RankingRow] = []
            for row_index in [idx - 2, idx - 1, idx]:
                if 0 <= row_index < len(ordered):
                    _, name, value, extra = ordered[row_index]
                    detail = f"{extra}" if extra is not None else None
                    ranking_rows.append(RankingRow(row_index + 1, name, value, detail))
            return ranking_rows, idx

    user_name = get_user_display_name(user_id, chat_id)
    user_dick = get_dick(user_id, chat_id)
    user_length = int(user_dick["length"] or 0)
    user_grow_date = user_dick.get("grow_date")

    if not user_grow_date:
        ranking_rows = []
        if ordered:
            _, name, value, _ = ordered[-1]
            ranking_rows.append(RankingRow(len(ordered), name, value, None))
        ranking_rows.append(RankingRow(0, user_name, user_length, "Не участвует в большой гонке"))
        return ranking_rows, None

    user_position = len(ordered) + 1
    ranking_rows = []
    if ordered:
        _, name, value, _ = ordered[-1]
        ranking_rows.append(RankingRow(len(ordered), name, value, None))
    ranking_rows.append(RankingRow(user_position, user_name, user_length, None))
    return ranking_rows, user_position


def _get_activity_streak_ranking(
    chat_id: int, user_id: int
) -> tuple[List[RankingRow], int]:
    today = date.today()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT u.user_id, u.name
            FROM users u
            WHERE u.chat_id = ?
            """,
            (chat_id,),
        )
        users = cur.fetchall()
        cur.execute(
            """
            SELECT user_id, date
            FROM daily_stats
            WHERE chat_id = ? AND messages > 0
            """,
            (chat_id,),
        )
        rows = cur.fetchall()

    dates_by_user: dict[int, set[date]] = {}
    for row in rows:
        row_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
        dates_by_user.setdefault(int(row["user_id"]), set()).add(row_date)

    computed_rows = []
    for user in users:
        user_id_value = int(user["user_id"])
        row_dates = dates_by_user.get(user_id_value, set())
        streak = 0
        expected = today
        while expected in row_dates:
            streak += 1
            expected -= timedelta(days=1)
        computed_rows.append((user_id_value, user["name"], streak, None))
    default_name = get_user_display_name(user_id, chat_id)
    return _get_ranking_rows(computed_rows, user_id, default_name)


def _get_reaction_conversion_ranking(
    chat_id: int, user_id: int
) -> tuple[List[RankingRow], int]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT u.user_id,
                   u.name,
                   COALESCE(t.react_taken, 0) as react_taken,
                   COALESCE(t.messages, 0) as messages
            FROM users u
            LEFT JOIN total_stats t ON t.user_id = u.user_id AND t.chat_id = u.chat_id
            WHERE u.chat_id = ?
            """,
            (chat_id,),
        )
        rows = cur.fetchall()

    computed_rows = []
    for row in rows:
        messages = int(row["messages"] or 0)
        react_taken = int(row["react_taken"] or 0)
        value = float(react_taken) / float(messages or 1)
        computed_rows.append((row["user_id"], row["name"], value, None))
    default_name = get_user_display_name(user_id, chat_id)
    return _get_ranking_rows(computed_rows, user_id, default_name)


def _get_peak_hours(chat_id: int, user_id: int) -> list[int]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages_reactions'"
        )
        if not cur.fetchone():
            return [0] * 24

        cur.execute("PRAGMA table_info(messages_reactions)")
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
                FROM messages_reactions
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

    base_y = 0.62
    step = 0.2
    for idx, row in enumerate(rows):
        alpha = 1.0
        if idx in (0, 2) and len(rows) > 1:
            alpha = 0.45
        detail = f" ({detail_label}: {row.detail})" if detail_label and row.detail else ""
        value_display = f"{row.value:.2f}" if isinstance(row.value, float) else str(row.value)
        prefix = f"{row.position}. " if row.position > 0 else ""
        line = f"{prefix}{row.name} — {value_display} {label_suffix}{detail}"
        ax.text(
            0.02,
            base_y - idx * step,
            line,
            fontsize=11,
            alpha=alpha,
            color="black",
        )


def _draw_dashboard(
    chat_id: int,
    user_id: int,
    user_name: str,
) -> bytes:
    flood_stats = _get_last_days_messages(user_id, chat_id)
    react_taken, react_given = _get_reaction_totals(user_id, chat_id)
    coffee_rows, coffee_pos = _get_coffee_ranking(chat_id, user_id)
    message_rows, message_pos = _get_message_ranking(chat_id, user_id)
    avg_len_rows, avg_len_pos = _get_avg_message_length_ranking(chat_id, user_id)
    streak_rows, streak_pos = _get_activity_streak_ranking(chat_id, user_id)
    conversion_rows, conversion_pos = _get_reaction_conversion_ranking(chat_id, user_id)
    dick_rows, dick_pos = _get_dick_length_ranking(chat_id, user_id)
    peak_hours = _get_peak_hours(chat_id, user_id)

    fig = plt.figure(figsize=(12, 9.0), dpi=120)
    fig.patch.set_facecolor("#FF47D1")
    grid = fig.add_gridspec(
        5,
        2,
        height_ratios=[1.2, 1, 1, 0.9, 1.1],
        hspace=0.6,
        wspace=0.3,
    )

    ax_flood = fig.add_subplot(grid[0, 0])
    ax_react = fig.add_subplot(grid[0, 1])
    ax_coffee = fig.add_subplot(grid[1, 0])
    ax_dick = fig.add_subplot(grid[1, 1])
    ax_messages = fig.add_subplot(grid[2, 0])
    ax_avg_len = fig.add_subplot(grid[2, 1])
    ax_streak = fig.add_subplot(grid[3, 0])
    ax_conversion = fig.add_subplot(grid[3, 1])
    ax_peak = fig.add_subplot(grid[4, :])

    fig.suptitle(f"Дашборд {user_name}", fontsize=16, fontweight="bold", y=0.98)
    fig.subplots_adjust(top=0.9)

    dates = [item["date"] for item in flood_stats]
    messages = [item["messages"] for item in flood_stats]
    date_values = [datetime.fromisoformat(d).date() for d in dates]
    ax_flood.plot(date_values, messages, color="#9c6ade", linewidth=2)
    ax_flood.fill_between(date_values, messages, color="#d4b8ff", alpha=0.5)
    ax_flood.set_title("Флуд за 2 недели (сообщения по дням)", fontsize=11)
    ax_flood.tick_params(axis="x", labelrotation=45, labelsize=8)
    ax_flood.tick_params(axis="y", labelsize=8)
    ax_flood.grid(alpha=0.2)
    ax_flood.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))

    ax_react.set_title("Реакции за всё время", fontsize=11)
    if react_taken + react_given == 0:
        ax_react.text(0.5, 0.5, "Нет реакций", ha="center", va="center", fontsize=12)
        ax_react.axis("off")
    else:
        wedges, _, _ = ax_react.pie(
            [react_taken, react_given],
            labels=None,
            autopct="%1.0f%%",
            colors=["#ff8fab", "#ffd6a5"],
            textprops={"fontsize": 9},
            center=(-0.25, 0.0),
        )
        ax_react.axis("equal")
        ax_react.legend(
            wedges,
            ["Получено", "Поставлено"],
            loc="center left",
            bbox_to_anchor=(0.65, 0.5),
            frameon=False,
            fontsize=9,
        )

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
    )

    _format_ranking_block(
        ax_avg_len,
        f"Средняя длина сообщений (место {avg_len_pos})",
        avg_len_rows,
        "симв.",
    )

    _format_ranking_block(
        ax_streak,
        f"Серия дней в чате (место {streak_pos})",
        streak_rows,
        "дн.",
    )
    _format_ranking_block(
        ax_conversion,
        f"Реакций на сообщение (место {conversion_pos})",
        conversion_rows,
        "реакц./сообщ.",
    )
    dick_title = "Длина члена (Не участвует в большой гонке)" if dick_pos is None else f"Длина члена (место {dick_pos})"
    _format_ranking_block(
        ax_dick,
        dick_title,
        dick_rows,
        "см",
    )

    ax_peak.set_title("Пиковые часы активности", fontsize=11)
    hours = list(range(24))
    ax_peak.bar(hours, peak_hours, color="#90dbf4")
    ax_peak.set_xticks(hours)
    ax_peak.set_xticklabels([str(h) for h in hours], fontsize=7)
    ax_peak.tick_params(axis="y", labelsize=8)
    ax_peak.set_xlabel("Час", fontsize=9)
    ax_peak.set_ylabel("Сообщений", fontsize=9)
    ax_peak.grid(axis="y", alpha=0.2)

    chart_face_color = "#F673E2"
    for axis in (ax_flood, ax_react, ax_peak):
        axis.set_facecolor(chart_face_color)

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
