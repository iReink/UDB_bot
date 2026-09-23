"""Shared tapeworm state and Telegram entry points."""

import asyncio
import html
import logging
import math
import random
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import db
from sits import format_sits


DOCTOR_ID = 1235654176
CURE_PRICE = 50
PARTIAL_CURE_PRICE = 10
PARTIAL_CURE_FACTOR = Decimal("0.8")
INITIAL_LENGTH = 5.0
INSTRUCTION = (
    "Цепень будет есть твой сит и расти. Его может вылечить официальный дядя "
    "доктор или коммерческий доктор в магазине /shop"
)
PRIMARY_CHANCES = {"geyser": .025, "coffee": .004, "round": .003, "sticker": .0006}
PAIR_CHANCES = {
    "sos": (.45, .05),
    "shpeh": (.49, .06),
    "bite": (.475, .015),
    "duel": (.10, .10),
}
GROUP_CHANCES = {"group_participant": .075, "group_spectator": .025, "daily": .075}
REPLY_CHANCE = .0025
_THOUSANDTH = Decimal("0.001")
HOST_PHRASES_PATH = Path(__file__).resolve().parent / "docs" / "cepen-host-phrases.md"
HOST_MESSAGE_START_HOUR = 10
HOST_MESSAGE_END_HOUR = 23
HOST_MESSAGE_SIGNATURE = "– твой цепень ❤️"
CEPEN_NAME_MAX_LENGTH = 32
CEPEN_NAME_DELETE_MARKS = frozenset("-‐‑‒–—―−﹣－")
SCRATCH_REWARD = 0.1
SCRATCH_DAILY_LIMIT = 50
SCRATCH_USER_DAILY_LIMIT = 5
_SCRATCH_MENU_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}


class CepenNameStates(StatesGroup):
    waiting_for_name = State()


@dataclass(frozen=True)
class GrowthPlan:
    mode: str
    old: float
    new: float
    gain: float
    cost: float
    full_new: float
    full_gain: float
    full_cost: float
    minimum_cost: float
    coefficient: float


@dataclass(frozen=True)
class HostMessage:
    message_date: str
    chat_id: int
    user_id: int
    phrase: str


@dataclass(frozen=True)
class ScratchResult:
    status: str
    total_count: int
    scratcher_count: int

GROWTH_LINES = (
    "{name}: {worm} сходил в /shop за питанием — {length} см (+{gain}), −{cost} сит.",
    "{name}: {worm} провёл дейлик внутри хозяина — {length} см (+{gain}), −{cost} сит.",
    "{name}: {worm} получил +{gain} см от маленького гейзера в животе. Теперь {length} см, −{cost} сит.",
    "{name}: {worm} поймал внутренний сит и вытянулся до {length} см (+{gain}), −{cost} сит.",
    "{name}: вместо кофе {worm} выпил сит — {length} см (+{gain}), −{cost} сит.",
    "{name}: за ночь {worm} нафармил +{gain} см. Длина {length} см, цена {cost} сит.",
    "{name}: {worm} победил в споре с желудком — {length} см (+{gain}), −{cost} сит.",
    "{name}: {worm} посмотрел кружочек и стал длиннее: {length} см (+{gain}), −{cost} сит.",
    "{name}: {worm} нашёл под внутренним стикером +{gain} см. Теперь {length} см, −{cost} сит.",
    "{name}: {worm} оформил подписку на рост — {length} см (+{gain}), −{cost} сит.",
    "{name}: чат флудил, {worm} не отставал — {length} см (+{gain}), −{cost} сит.",
    "{name}: {worm} занял первое место в рейтинге самого себя: {length} см (+{gain}), −{cost} сит.",
    "{name}: пока все считали сообщения, {worm} насчитал {length} см (+{gain}), −{cost} сит.",
    "{name}: {worm} заглянул к дяде доктору, но выбрал буфет — {length} см (+{gain}), −{cost} сит.",
    "{name}: {worm} сделал зарядку в форме буквы С — {length} см (+{gain}), −{cost} сит.",
    "{name}: {worm} проснулся раньше гейзера — {length} см (+{gain}), −{cost} сит.",
    "{name}: {worm} обновил личный рекорд: {length} см (+{gain}), −{cost} сит.",
    "{name}: {worm} получил сит за вредное дело — {length} см (+{gain}), −{cost} сит.",
    "{name}: {worm} успешно закрыл дейлик роста: {length} см (+{gain}), −{cost} сит.",
    "{name}: {worm} выиграл битву за место в животе — {length} см (+{gain}), −{cost} сит.",
)


@lru_cache(maxsize=1)
def load_host_phrases(path: str | Path | None = None) -> tuple[str, ...]:
    source = Path(path) if path is not None else HOST_PHRASES_PATH
    phrases = []
    for line in source.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*\d+\.\s+(.+?)\s*$", line)
        if match:
            phrase = match.group(1)
            if "{nickname}" not in phrase:
                raise ValueError(f"Tapeworm host phrase has no {{nickname}} placeholder: {phrase}")
            phrases.append(phrase)
    if not phrases:
        raise ValueError(f"No tapeworm host phrases found in {source}")
    return tuple(phrases)


def normalize_name_input(value: str | None) -> tuple[str, str | None]:
    normalized = " ".join((value or "").split())
    if len(normalized) == 1 and normalized in CEPEN_NAME_DELETE_MARKS:
        return "delete", None
    if not normalized:
        return "empty", None
    if len(normalized) > CEPEN_NAME_MAX_LENGTH:
        return "too_long", None
    return "name", normalized


def reply_exposure_source_id(message) -> int | None:
    """Return the replied user's id only for an ordinary user reply.

    Bot commands can retain a Telegram reply context (for example when opened
    while the composer is replying to a message), but invoking bot UI is not a
    reply exposure in the game mechanic.
    """
    sender = getattr(message, "from_user", None)
    replied = getattr(message, "reply_to_message", None)
    source = getattr(replied, "from_user", None)
    if not sender or getattr(sender, "is_bot", False):
        return None
    if not source or getattr(source, "is_bot", False) or source.id == sender.id:
        return None

    text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    entities = getattr(message, "entities", None) or getattr(message, "caption_entities", None) or []
    for entity in entities:
        entity_type = getattr(entity, "type", None)
        entity_type = getattr(entity_type, "value", entity_type)
        if getattr(entity, "offset", None) == 0 and entity_type == "bot_command":
            return None
    if re.match(r"^/[A-Za-z0-9_]+(?:@[A-Za-z0-9_]+)?(?:\s|$)", text):
        return None
    return int(source.id)


def _stored_name(conn, chat_id: int, user_id: int) -> str | None:
    try:
        row = conn.execute(
            "SELECT cepen_name FROM users WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    value = str(row["cepen_name"] or "").strip() if row else ""
    return value or None


def name(chat_id: int, user_id: int) -> str | None:
    with closing(db.get_connection()) as conn:
        return _stored_name(conn, chat_id, user_id)


def subject_from_name(value: str | None, *, capital: bool = False, html_mode: bool = False) -> str:
    word = "Цепень" if capital else "цепень"
    if not value:
        return word
    safe_name = html.escape(value) if html_mode else value
    return f"{word} {safe_name}"


def _genitive_from_name(value: str | None, *, capital: bool = False, html_mode: bool = False) -> str:
    word = "Цепня" if capital else "цепня"
    if not value:
        return word
    safe_name = html.escape(value) if html_mode else value
    return f"{word} по имени {safe_name}"


def label(
    chat_id: int,
    user_id: int,
    *,
    capital: bool = False,
    html_mode: bool = False,
) -> str:
    return subject_from_name(name(chat_id, user_id), capital=capital, html_mode=html_mode)


def set_name(chat_id: int, user_id: int, value: str | None) -> str:
    """Set or delete a live tapeworm name; return named, deleted, healthy, or disabled."""
    with closing(db.get_connection()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not db.cepen_enabled(chat_id, conn):
            return "disabled"
        if _status(conn, chat_id, user_id) <= 0:
            return "healthy"
        conn.execute(
            "UPDATE users SET cepen_name=? WHERE chat_id=? AND user_id=?",
            (value or None, chat_id, user_id),
        )
        conn.commit()
    return "deleted" if not value else "named"


def render_host_message(
    phrase: str,
    host_mention: str,
    cepen_name: str | None = None,
) -> str:
    safe_phrase = html.escape(phrase).replace("{nickname}", host_mention)
    if cepen_name:
        signature = f"– твой цепень {html.escape(cepen_name)} ❤️"
    else:
        signature = HOST_MESSAGE_SIGNATURE
    return f"{safe_phrase}\n{signature}"


def _host_message_window(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(hour=HOST_MESSAGE_START_HOUR, minute=0, second=0, microsecond=0)
    end = now.replace(hour=HOST_MESSAGE_END_HOUR, minute=0, second=0, microsecond=0)
    return start, end


def schedule_host_messages(now: datetime) -> int:
    """Create today's durable random schedule for every currently eligible host."""
    start, end = _host_message_window(now)
    if now >= end:
        return 0
    earliest = max(now, start).replace(microsecond=0)
    latest = end - timedelta(seconds=1)
    available_seconds = max(0, int((latest - earliest).total_seconds()))
    phrases = load_host_phrases()
    created = 0
    with closing(db.get_connection()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        hosts = conn.execute(
            "SELECT chat_id,user_id FROM users WHERE chat_id<0 AND COALESCE(cepen,0)>0"
        ).fetchall()
        for host in hosts:
            chat_id, user_id = int(host["chat_id"]), int(host["user_id"])
            if not db.cepen_enabled(chat_id, conn):
                continue
            scheduled_at = earliest + timedelta(seconds=random.randint(0, available_seconds))
            result = conn.execute(
                "INSERT OR IGNORE INTO cepen_daily_messages("
                "message_date,chat_id,user_id,scheduled_at,phrase) VALUES (?,?,?,?,?)",
                (
                    now.date().isoformat(), chat_id, user_id,
                    scheduled_at.strftime("%Y-%m-%d %H:%M:%S"), random.choice(phrases),
                ),
            )
            created += result.rowcount
        conn.commit()
    return created


def due_host_messages(now: datetime) -> list[HostMessage]:
    """Return eligible unsent messages due inside today's server-time window."""
    start, end = _host_message_window(now)
    if now < start or now >= end:
        return []
    date_key = now.date().isoformat()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    with closing(db.get_connection()) as conn:
        rows = conn.execute(
            "SELECT m.message_date,m.chat_id,m.user_id,m.phrase "
            "FROM cepen_daily_messages m "
            "JOIN users u ON u.chat_id=m.chat_id AND u.user_id=m.user_id "
            "WHERE m.message_date=? AND m.sent_at IS NULL AND m.scheduled_at<=? "
            "AND m.chat_id<0 AND COALESCE(u.cepen,0)>0 "
            "ORDER BY m.scheduled_at,m.chat_id,m.user_id",
            (date_key, timestamp),
        ).fetchall()
        return [
            HostMessage(
                str(row["message_date"]), int(row["chat_id"]),
                int(row["user_id"]), str(row["phrase"]),
            )
            for row in rows
            if db.cepen_enabled(int(row["chat_id"]), conn)
        ]


def mark_host_message_sent(message: HostMessage, now: datetime) -> bool:
    with closing(db.get_connection()) as conn:
        result = conn.execute(
            "UPDATE cepen_daily_messages SET sent_at=? "
            "WHERE message_date=? AND chat_id=? AND user_id=? AND sent_at IS NULL",
            (
                now.strftime("%Y-%m-%d %H:%M:%S"), message.message_date,
                message.chat_id, message.user_id,
            ),
        )
        conn.commit()
        return result.rowcount == 1


async def dispatch_host_messages(bot, now: datetime) -> int:
    schedule_host_messages(now)
    sent = 0
    for message in due_host_messages(now):
        text = render_host_message(
            message.phrase,
            mention(message.chat_id, message.user_id),
            name(message.chat_id, message.user_id),
        )
        try:
            await bot.send_message(message.chat_id, text, parse_mode="HTML")
        except Exception:
            logging.exception(
                "cepen host message failed for chat=%s user=%s",
                message.chat_id, message.user_id,
            )
            continue
        if mark_host_message_sent(message, now):
            sent += 1
    return sent


async def host_message_loop(bot):
    while True:
        try:
            await dispatch_host_messages(bot, datetime.now().astimezone())
        except Exception:
            logging.exception("cepen host message scheduler failed")
        await asyncio.sleep(30)


def _round_length(value: Decimal) -> float:
    return float(value.quantize(_THOUSANDTH, rounding=ROUND_HALF_UP))


def growth_preview(length: float) -> tuple[float, float, float]:
    old = Decimal(str(length))
    new = _round_length(old * Decimal("1.15"))
    cost = _round_length(old * Decimal("0.1"))
    return new, _round_length(Decimal(str(new)) - old), cost


def growth_plan(length: float, balance: float) -> GrowthPlan:
    old = Decimal(str(length))
    available = max(Decimal("0"), Decimal(str(balance)))
    full_new, full_gain, full_cost = growth_preview(length)
    full_cost_decimal = Decimal(str(full_cost))
    minimum_cost = _round_length(full_cost_decimal * Decimal("0.1"))
    if available >= full_cost_decimal:
        return GrowthPlan(
            "full", length, full_new, full_gain, full_cost,
            full_new, full_gain, full_cost, minimum_cost, 1.0,
        )
    if available >= Decimal(str(minimum_cost)) and full_cost_decimal > 0:
        actual_cost = _round_length(available)
        coefficient = Decimal(str(actual_cost)) / full_cost_decimal
        gain = _round_length(old * Decimal("0.15") * coefficient)
        new = _round_length(old + Decimal(str(gain)))
        return GrowthPlan(
            "partial", length, new, gain, actual_cost,
            full_new, full_gain, full_cost, minimum_cost, float(coefficient),
        )
    return GrowthPlan(
        "anabiosis", length, length, 0.0, 0.0,
        full_new, full_gain, full_cost, minimum_cost, 0.0,
    )


def _mention(conn, chat_id: int, user_id: int) -> str:
    row = conn.execute(
        "SELECT nick, name FROM users WHERE chat_id=? AND user_id=?", (chat_id, user_id)
    ).fetchone()
    nick = str(row["nick"] or "").strip().lstrip("@") if row else ""
    if nick:
        return "@" + html.escape(nick)
    name = str(row["name"] or user_id) if row else str(user_id)
    return f'<a href="tg://user?id={user_id}">{html.escape(name)}</a>'


def mention(chat_id: int, user_id: int) -> str:
    with closing(db.get_connection()) as conn:
        return _mention(conn, chat_id, user_id)


def _status(conn, chat_id: int, user_id: int) -> float:
    row = conn.execute("SELECT cepen FROM users WHERE chat_id=? AND user_id=?", (chat_id, user_id)).fetchone()
    return float(row["cepen"] or 0) if row else 0.0


def length(chat_id: int, user_id: int) -> float:
    with closing(db.get_connection()) as conn:
        return _status(conn, chat_id, user_id)


def ranking_rows(chat_id: int) -> list[tuple[int, float, str | None]]:
    with closing(db.get_connection()) as conn:
        rows = conn.execute(
            "SELECT user_id,cepen,cepen_name FROM users "
            "WHERE chat_id=? AND COALESCE(cepen,0)>0 "
            "ORDER BY cepen DESC,user_id ASC",
            (chat_id,),
        ).fetchall()
    return [
        (
            int(row["user_id"]),
            float(row["cepen"]),
            str(row["cepen_name"] or "").strip() or None,
        )
        for row in rows
    ]


def ranking_text(chat_id: int, full: bool = False) -> tuple[str, int]:
    rows = ranking_rows(chat_id)
    if not rows:
        return "🏆 В этом чате пока нет цепней.", 0
    shown = rows if full else rows[:10]
    lines = ["🏆 Рейтинг цепней:"]
    for place, (user_id, cepen_length, cepen_name) in enumerate(shown, start=1):
        owner_name = db.get_user_display_name(user_id, chat_id)
        named_part = f" — {cepen_name}" if cepen_name else ""
        lines.append(
            f"{place}. {owner_name}{named_part} — {format_sits(cepen_length)} см"
        )
    return "\n".join(lines), len(rows)


def _scratch_counts(
    conn,
    chat_id: int,
    owner_id: int,
    scratcher_id: int,
    date_key: str,
) -> tuple[int, int]:
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN scratcher_id=? THEN 1 ELSE 0 END) AS personal "
        "FROM cepen_scratches WHERE chat_id=? AND owner_id=? AND scratch_date=?",
        (scratcher_id, chat_id, owner_id, date_key),
    ).fetchone()
    return int(row["total"] or 0), int(row["personal"] or 0)


def scratch(
    chat_id: int,
    owner_id: int,
    scratcher_id: int,
    callback_query_id: str,
    *,
    date_key: str | None = None,
) -> ScratchResult:
    """Atomically record one rewarded scratch and enforce both daily limits."""
    day = date_key or datetime.now().date().isoformat()
    with closing(db.get_connection()) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            if not db.cepen_enabled(chat_id, conn):
                conn.rollback()
                return ScratchResult("disabled", 0, 0)
            if _status(conn, chat_id, owner_id) <= 0:
                conn.rollback()
                return ScratchResult("healthy", 0, 0)
            if scratcher_id == owner_id:
                total, personal = _scratch_counts(conn, chat_id, owner_id, scratcher_id, day)
                conn.rollback()
                return ScratchResult("self", total, personal)

            duplicate = conn.execute(
                "SELECT 1 FROM cepen_scratches WHERE callback_query_id=?",
                (str(callback_query_id),),
            ).fetchone()
            total, personal = _scratch_counts(conn, chat_id, owner_id, scratcher_id, day)
            if duplicate:
                conn.rollback()
                return ScratchResult("duplicate", total, personal)
            if total >= SCRATCH_DAILY_LIMIT:
                conn.rollback()
                return ScratchResult("worm_limit", total, personal)
            if personal >= SCRATCH_USER_DAILY_LIMIT:
                conn.rollback()
                return ScratchResult("user_limit", total, personal)

            now = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                "INSERT INTO cepen_scratches("
                "callback_query_id,scratch_date,chat_id,owner_id,scratcher_id,reward,created_at"
                ") VALUES (?,?,?,?,?,?,?)",
                (
                    str(callback_query_id), day, chat_id, owner_id, scratcher_id,
                    SCRATCH_REWARD, now,
                ),
            )
            db.apply_sit_change(
                conn,
                chat_id,
                owner_id,
                SCRATCH_REWARD,
                action_code="cepen_scratch_reward",
                action_ru="Награда за чесание цепня",
                metadata={
                    "scratcher_id": scratcher_id,
                    "scratch_date": day,
                    "callback_query_id": str(callback_query_id),
                },
            )
            conn.commit()
            return ScratchResult("scratched", total + 1, personal + 1)
        except Exception:
            conn.rollback()
            raise


def scratch_summary(
    chat_id: int,
    owner_id: int,
    *,
    date_key: str | None = None,
) -> tuple[list[tuple[str, int]], int]:
    day = date_key or datetime.now().date().isoformat()
    with closing(db.get_connection()) as conn:
        rows = conn.execute(
            "SELECT s.scratcher_id,COUNT(*) AS count,MIN(s.created_at) AS first_scratch,"
            "COALESCE(NULLIF(u.name,''),NULLIF(u.nick,''),CAST(s.scratcher_id AS TEXT)) AS name "
            "FROM cepen_scratches s LEFT JOIN users u "
            "ON u.chat_id=s.chat_id AND u.user_id=s.scratcher_id "
            "WHERE s.chat_id=? AND s.owner_id=? AND s.scratch_date=? "
            "GROUP BY s.scratcher_id,name ORDER BY first_scratch,s.scratcher_id",
            (chat_id, owner_id, day),
        ).fetchall()
    result = [(str(row["name"]), int(row["count"])) for row in rows]
    return result, sum(count for _, count in result)


def _scratch_summary_text(chat_id: int, owner_id: int) -> str:
    rows, total = scratch_summary(chat_id, owner_id)
    if not rows:
        return ""
    people = ", ".join(f"{person} ({count})" for person, count in rows)
    return (
        f"\n\nСегодня чесали: {people}\n"
        f"Получено {format_sits(total * SCRATCH_REWARD)} сит."
    )


def menu_keyboard(
    user_id: int,
    has_cepen: bool,
    cepen_name: str | None = None,
    scratch_count: int = 0,
) -> InlineKeyboardMarkup:
    buttons = []
    if has_cepen:
        buttons.append([
            InlineKeyboardButton(
                text=(
                    f"Почесать {cepen_name} [{scratch_count}/{SCRATCH_DAILY_LIMIT}]"
                    if cepen_name
                    else f"Почесать цепня [{scratch_count}/{SCRATCH_DAILY_LIMIT}]"
                ),
                callback_data=f"cepen:scratch:{user_id}",
            )
        ])
        buttons.append([
            InlineKeyboardButton(
                text="Изменить имя цепня" if cepen_name else "Дать имя цепню",
                callback_data=f"cepen:name:{user_id}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(
            text="Рейтинг цепней", callback_data=f"cepen:rating:{user_id}"
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def rating_keyboard(user_id: int, total: int, full: bool = False) -> InlineKeyboardMarkup | None:
    if full or total <= 10:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Полный рейтинг", callback_data=f"cepen:rating_full:{user_id}"
        )
    ]])


def _infect(conn, chat_id: int, user_id: int) -> bool:
    created = conn.execute(
        "INSERT OR IGNORE INTO users(user_id,chat_id,name,cepen) VALUES (?,?,?,?)",
        (user_id, chat_id, str(user_id), INITIAL_LENGTH),
    )
    if created.rowcount == 1:
        return True
    cur = conn.execute(
        "UPDATE users SET cepen=?,cepen_name=NULL "
        "WHERE chat_id=? AND user_id=? AND COALESCE(cepen,0)=0",
        (INITIAL_LENGTH, chat_id, user_id),
    )
    return cur.rowcount == 1


def _primary_text(conn, chat_id: int, user_id: int, kind: str) -> str:
    name = _mention(conn, chat_id, user_id)
    phrases = {
        "geyser": f"{name} нашёл на дне цепня и успешно подхватил его",
        "coffee": f"В кофе было что-то странное. {name} подхватил цепня",
        "round": f"Пока {name} записывал кружок, через дырку в экране прополз цепень",
        "sticker": f"Под стикером мирно спал цепень. Теперь он спит внутри {name}",
    }
    return phrases[kind] + "\n\n" + INSTRUCTION


def attempt_primary(chat_id: int, user_id: int, kind: str) -> str | None:
    if chat_id >= 0:
        return None
    chance = PRIMARY_CHANCES[kind]
    with closing(db.get_connection()) as conn:
        if not db.cepen_enabled(chat_id, conn):
            return None
        if _status(conn, chat_id, user_id) > 0 or random.random() >= chance:
            return None
        conn.execute("BEGIN IMMEDIATE")
        if not db.cepen_enabled(chat_id, conn) or _status(conn, chat_id, user_id) > 0:
            return None
        if not _infect(conn, chat_id, user_id):
            return None
        text = _primary_text(conn, chat_id, user_id, kind)
        conn.commit()
        return text


def _secondary_text(conn, chat_id: int, target: int, source: int, kind: str, source_initiated=False) -> str:
    ill = _mention(conn, chat_id, target)
    carrier = _mention(conn, chat_id, source)
    source_name = _stored_name(conn, chat_id, source)
    source_subject = subject_from_name(source_name, html_mode=True)
    source_genitive = _genitive_from_name(source_name, html_mode=True)
    phrases = {
        "group_participant": (
            f"{ill} подрочил вместе с заражённым {carrier} и подхватил {source_genitive}"
        ),
        "group_spectator": (
            f"{ill} подглядывал за заражённым {carrier} и подхватил {source_genitive}"
        ),
        "duel": (
            f"{ill} так сильно скрестил шпагу, что {source_subject} владельца "
            f"{carrier} переполз по мостику"
        ),
        "daily": (
            f"{ill} присел на дейлике рядом с {carrier} и заразился от {source_genitive}"
        ),
        "reply": (
            f"Ответив на сообщение, {ill} коснулся {source_genitive} владельца "
            f"{carrier}, и теперь цепня два."
        ),
    }
    if kind == "sos":
        phrase = (f"{carrier} всосал в {ill} {source_genitive}" if source_initiated
                  else f"{ill} засосал {carrier} и высосал себе {source_genitive}")
    elif kind == "shpeh":
        phrase = (f"{carrier} пошпёхал {ill} и подарил {source_genitive}" if source_initiated
                  else f"{ill} пошпёхался с {carrier} и получил {source_genitive} в качестве хеппиэндинга")
    elif kind == "bite":
        phrase = (f"{carrier} передал {source_genitive} пользователю {ill} через укус. Вампир хренов."
                  if source_initiated else f"{ill} откусил у {carrier} кусочек {source_genitive}")
    else:
        phrase = phrases[kind]
    return phrase + "\n\n" + INSTRUCTION


def attempt_secondary(chat_id: int, target: int, source: int, kind: str, chance: float, *, source_initiated=False) -> str | None:
    if chat_id >= 0 or target == source:
        return None
    with closing(db.get_connection()) as conn:
        if not db.cepen_enabled(chat_id, conn):
            return None
        if _status(conn, chat_id, source) <= 0 or _status(conn, chat_id, target) > 0:
            return None
        if random.random() >= chance:
            return None
        conn.execute("BEGIN IMMEDIATE")
        if (not db.cepen_enabled(chat_id, conn) or _status(conn, chat_id, source) <= 0
                or _status(conn, chat_id, target) > 0):
            return None
        if not _infect(conn, chat_id, target):
            return None
        text = _secondary_text(conn, chat_id, target, source, kind, source_initiated)
        conn.commit()
        return text


def attempt_pair(chat_id: int, initiator: int, partner: int, kind: str) -> str | None:
    if initiator == partner:
        return None
    with closing(db.get_connection()) as conn:
        source_initiated = _status(conn, chat_id, initiator) > 0
        partner_infected = _status(conn, chat_id, partner) > 0
    if source_initiated == partner_infected:
        return None
    high, low = PAIR_CHANCES[kind]
    target = partner if source_initiated else initiator
    source = initiator if source_initiated else partner
    return attempt_secondary(chat_id, target, source, kind, low if source_initiated else high,
                             source_initiated=source_initiated)


def attempt_event(chat_id: int, event_kind: str, event_id: str, participants: list[int], spectators: list[int] | None = None) -> list[str]:
    """One exposure per susceptible from carriers present before this event."""
    if chat_id >= 0:
        return []
    spectators = spectators or []
    people = list(dict.fromkeys([*participants, *spectators]))
    with closing(db.get_connection()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not db.cepen_enabled(chat_id, conn):
            return []
        result = conn.execute(
            "INSERT OR IGNORE INTO cepen_event_checks(event_kind,event_id,chat_id,checked_at) VALUES (?,?,?,?)",
            (event_kind, str(event_id), chat_id, datetime.now().isoformat(timespec="seconds")),
        )
        if result.rowcount == 0:
            return []
        sources = [uid for uid in participants if _status(conn, chat_id, uid) > 0]
        notices = []
        if sources:
            for uid in people:
                if uid in sources or _status(conn, chat_id, uid) > 0:
                    continue
                kind = "daily" if event_kind == "daily" else (
                    "group_participant" if uid in participants else "group_spectator"
                )
                if random.random() < GROUP_CHANCES[kind] and _infect(conn, chat_id, uid):
                    notices.append(_secondary_text(conn, chat_id, uid, random.choice(sources), kind))
        conn.commit()
        return notices


def cure(chat_id: int, user_id: int, *, price: float = 0) -> str:
    """Return cured, healthy, or insufficient; charge and cure in one transaction."""
    with closing(db.get_connection()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not db.cepen_enabled(chat_id, conn):
            return "disabled"
        if _status(conn, chat_id, user_id) <= 0:
            return "healthy"
        if price:
            try:
                db.apply_sit_change(conn, chat_id, user_id, -price,
                                    action_code="cepen_cure_purchase", action_ru="Лечение цепня",
                                    require_sufficient=True)
            except db.InsufficientSitsError:
                conn.rollback()
                return "insufficient"
        conn.execute("UPDATE users SET cepen=0,cepen_name=NULL,cepen_growth_date=NULL "
                     "WHERE chat_id=? AND user_id=?",
                     (chat_id, user_id))
        conn.commit()
        return "cured"


def partial_cure(chat_id: int, user_id: int) -> tuple[str, float | None, float | None]:
    """Reduce a live tapeworm by 20%, never below its initial length."""
    with closing(db.get_connection()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not db.cepen_enabled(chat_id, conn):
            return "disabled", None, None
        old = _status(conn, chat_id, user_id)
        if old <= 0:
            return "healthy", None, None
        if old <= INITIAL_LENGTH:
            return "minimum", old, old
        new = max(INITIAL_LENGTH, _round_length(Decimal(str(old)) * PARTIAL_CURE_FACTOR))
        try:
            db.apply_sit_change(
                conn, chat_id, user_id, -PARTIAL_CURE_PRICE,
                action_code="cepen_partial_cure", action_ru="Уменьшение цепня на 20%",
                metadata={"old_cm": old, "new_cm": new}, require_sufficient=True,
            )
        except db.InsufficientSitsError:
            conn.rollback()
            return "insufficient", old, old
        conn.execute(
            "UPDATE users SET cepen=? WHERE chat_id=? AND user_id=?", (new, chat_id, user_id)
        )
        conn.commit()
        return "reduced", old, new


def _dick_length(conn, chat_id: int, user_id: int) -> int:
    try:
        row = conn.execute(
            "SELECT length FROM dicks WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["length"] or 0) if row else 0


def _manual_text(_cepen_name: str | None = None) -> str:
    return (
        "\n\nЦепень поможет подрасти короткому члену, если сможет съесть сит, равный "
        "1/10 от своей текущей длины. В /shop можно вылечить себя от цепня. "
        "Друзья могут чесать твоего цепня и ты получишь сит."
    )


def status_text(chat_id: int, user_id: int) -> str:
    with closing(db.get_connection()) as conn:
        if not db.cepen_enabled(chat_id, conn):
            return "Цепень отключён в этом чате."
        row = conn.execute("SELECT cepen,sits,cepen_name FROM users WHERE chat_id=? AND user_id=?",
                           (chat_id, user_id)).fetchone()
        if not row or float(row["cepen"] or 0) <= 0:
            return "Цепня пока нет. Береги сит." + _manual_text()
        old = float(row["cepen"])
        balance = float(row["sits"] or 0)
        cepen_name = str(row["cepen_name"] or "").strip() or None
        plan = growth_plan(old, balance)
        dick_length = _dick_length(conn, chat_id, user_id)

    lines = [(
        f"🐛 Цепень {cepen_name}: {format_sits(old)} см"
        if cepen_name else f"🐛 Длина цепня: {format_sits(old)} см"
    )]
    if plan.mode == "full":
        lines.append(
            f"Прогноз на вечер: полный рост до {format_sits(plan.new)} см "
            f"(+{format_sits(plan.gain)}) за {format_sits(plan.cost)} сит."
        )
    elif plan.mode == "partial":
        percent = _round_length(Decimal(str(plan.coefficient)) * Decimal("100"))
        lines.append(
            f"Прогноз на вечер: частичный рост до {format_sits(plan.new)} см "
            f"(+{format_sits(plan.gain)}, {format_sits(percent)}% полного роста) "
            f"за {format_sits(plan.cost)} сит."
        )
    else:
        missing_minimum = _round_length(
            Decimal(str(plan.minimum_cost)) - Decimal(str(max(0.0, balance)))
        )
        lines.append(
            f"Прогноз на вечер: анабиоз. Для частичного роста нужно минимум "
            f"{format_sits(plan.minimum_cost)} сит, не хватает {format_sits(missing_minimum)}."
        )

    possible_bonus = math.floor(plan.full_cost) if plan.full_new > dick_length else 0
    if plan.mode == "full" and possible_bonus > 0:
        if cepen_name:
            lines.append(
                f"🍆 Благодаря персонажу по имени {cepen_name} член вырастет на "
                f"{possible_bonus} см."
            )
        else:
            lines.append(f"🍆 Член вырастет на {possible_bonus} см.")
    return (
        "\n".join(lines)
        + _manual_text(cepen_name)
        + _scratch_summary_text(chat_id, user_id)
    )


def grow_all(date_key: str) -> dict[int, list[str]]:
    import dick

    reports: dict[int, list[str]] = {}
    with closing(db.get_connection()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT user_id,chat_id,cepen,sits,cepen_name FROM users WHERE cepen>0 AND chat_id<0 "
            "AND COALESCE(cepen_growth_date,'')<>? ORDER BY chat_id,user_id", (date_key,)
        ).fetchall()
        for row in rows:
            chat_id, user_id = int(row["chat_id"]), int(row["user_id"])
            if not db.cepen_enabled(chat_id, conn):
                continue
            old = float(row["cepen"])
            cepen_name = str(row["cepen_name"] or "").strip() or None
            worm = subject_from_name(cepen_name, html_mode=True)
            worm_capital = subject_from_name(cepen_name, capital=True, html_mode=True)
            plan = growth_plan(old, float(row["sits"] or 0))
            name = _mention(conn, chat_id, user_id)
            if plan.mode in {"full", "partial"}:
                db.apply_sit_change(conn, chat_id, user_id, -plan.cost,
                                    action_code="cepen_growth", action_ru="Рост цепня",
                                    metadata={
                                        "growth_date": date_key,
                                        "old_cm": old,
                                        "new_cm": plan.new,
                                        "mode": plan.mode,
                                        "coefficient": plan.coefficient,
                                    },
                                    require_sufficient=True)
                conn.execute("UPDATE users SET cepen=?,cepen_growth_date=? WHERE chat_id=? AND user_id=?",
                             (plan.new, date_key, chat_id, user_id))
                if plan.mode == "full":
                    dick_length = _dick_length(conn, chat_id, user_id)
                    dick_bonus = math.floor(plan.cost) if plan.new > dick_length else 0
                    if dick_bonus > 0:
                        dick.apply_dick_length_change(
                            conn, user_id, chat_id, dick_bonus, date_value=date_key
                        )
                    line = random.choice(GROWTH_LINES).format(
                        name=name,
                        worm=worm,
                        length=format_sits(plan.new),
                        gain=format_sits(plan.gain),
                        cost=format_sits(plan.cost),
                    )
                    if dick_bonus > 0:
                        line += f" Член не выдержал конкуренции и вырос на {dick_bonus} см."
                else:
                    percent = _round_length(Decimal(str(plan.coefficient)) * Decimal("100"))
                    line = (
                        f"{name}: {worm} выгреб весь доступный сит — {format_sits(plan.cost)} — "
                        f"и вырос частично до {format_sits(plan.new)} см "
                        f"(+{format_sits(plan.gain)}, {format_sits(percent)}% полного роста). "
                        "На рост члена сил не осталось."
                    )
            else:
                daily = conn.execute("SELECT messages FROM daily_stats WHERE chat_id=? AND user_id=? AND date=?",
                                     (chat_id, user_id, date_key)).fetchone()
                count = int(daily["messages"] or 0) if daily else 0
                loss = count - count // 2
                if loss:
                    conn.execute("UPDATE daily_stats SET messages=messages-? WHERE chat_id=? AND user_id=? AND date=?",
                                 (loss, chat_id, user_id, date_key))
                    conn.execute("UPDATE total_stats SET messages=MAX(0,messages-?) WHERE chat_id=? AND user_id=?",
                                 (loss, chat_id, user_id))
                conn.execute("UPDATE users SET cepen_growth_date=? WHERE chat_id=? AND user_id=?",
                             (date_key, chat_id, user_id))
                line = f"У {name} недостаточно сит для роста, поэтому {worm} в анабиозе."
                if count:
                    line += (
                        f" Для поддержания жизнедеятельности {worm_capital} съел половину сообщений."
                    )
            reports.setdefault(chat_id, []).append(line)
        conn.commit()
    return reports


async def growth_loop(bot):
    local_tz = timezone(timedelta(hours=5))
    while True:
        now = datetime.now(local_tz)
        run_at = now.replace(hour=23, minute=59, second=0, microsecond=0)
        if now < run_at:
            await asyncio.sleep((run_at - now).total_seconds())
        try:
            reports = grow_all(run_at.date().isoformat())
            for chat_id, lines in reports.items():
                await bot.send_message(chat_id, "🐛 Цепни подвели итоги дня:\n" + "\n".join(lines),
                                       parse_mode="HTML")
        except Exception:
            logging.exception("cepen growth failed")
        next_run = run_at + timedelta(days=1)
        await asyncio.sleep(max(1, (next_run - datetime.now(local_tz)).total_seconds()))


def due_daily_exposures(now: datetime) -> list[tuple[int, list[str]]]:
    """Check meetings in their five-minute start window, once per meeting."""
    upper = now.strftime("%Y-%m-%d %H:%M")
    lower = (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
    with closing(db.get_connection()) as conn:
        events = conn.execute(
            "SELECT id,chat_id FROM daily_events WHERE date||' '||time BETWEEN ? AND ?",
            (lower, upper),
        ).fetchall()
        event_people = [
            (int(event["id"]), int(event["chat_id"]), [int(row[0]) for row in conn.execute(
                "SELECT user_id FROM daily_participants WHERE daily_id=?", (event["id"],)
            )])
            for event in events
        ]
    result = []
    for event_id, chat_id, participants in event_people:
        notices = attempt_event(chat_id, "daily", str(event_id), participants)
        if notices:
            result.append((chat_id, notices))
    return result


async def daily_exposure_loop(bot):
    while True:
        try:
            local_now = datetime.now(timezone(timedelta(hours=5))).replace(tzinfo=None)
            for chat_id, notices in due_daily_exposures(local_now):
                for notice in notices:
                    await bot.send_message(chat_id, notice, parse_mode="HTML")
        except Exception:
            logging.exception("cepen daily exposure failed")
        await asyncio.sleep(30)


def _target_by_nick(chat_id: int, nickname: str) -> int | None:
    nick = nickname.strip().lstrip("@").lower()
    if not nick:
        return None
    with closing(db.get_connection()) as conn:
        row = conn.execute(
            "SELECT user_id FROM users WHERE chat_id=? AND LOWER(REPLACE(nick,'@',''))=?",
            (chat_id, nick),
        ).fetchone()
    return int(row["user_id"]) if row else None


def register_handlers(dp):
    @dp.message(Command("cepen"))
    async def cepen_command(message):
        chat_id, user_id = message.chat.id, message.from_user.id
        enabled = db.cepen_enabled(chat_id)
        cepen_name = name(chat_id, user_id) if enabled else None
        _, scratch_count = scratch_summary(chat_id, user_id) if enabled else ([], 0)
        keyboard = (
            menu_keyboard(
                user_id,
                length(chat_id, user_id) > 0,
                cepen_name,
                scratch_count,
            )
            if enabled else None
        )
        await message.reply(status_text(chat_id, user_id), reply_markup=keyboard)

    @dp.callback_query(lambda query: query.data and query.data.startswith("cepen:"))
    async def cepen_menu_callback(query, state: FSMContext):
        parts = query.data.split(":")
        if len(parts) != 3 or not parts[2].isdigit():
            await query.answer()
            return
        action, owner_id = parts[1], int(parts[2])
        chat_id = query.message.chat.id
        if not db.cepen_enabled(chat_id):
            await query.answer("Цепень отключён в этом чате.", show_alert=True)
            return
        if action == "scratch":
            lock = _SCRATCH_MENU_LOCKS.setdefault(
                (chat_id, owner_id), asyncio.Lock()
            )
            async with lock:
                result = scratch(
                    chat_id,
                    owner_id,
                    query.from_user.id,
                    query.id,
                )
                if result.status == "disabled":
                    await query.answer("Цепень отключён в этом чате.", show_alert=True)
                    return
                if result.status == "healthy":
                    await query.answer("У владельца больше нет цепня.", show_alert=True)
                    return
                if result.status == "self":
                    cepen_name = name(chat_id, owner_id)
                    response = (
                        f"{cepen_name}: «Спасибо, очень приятно!»"
                        if cepen_name else "Спасибо, очень приятно!"
                    )
                    await query.answer(response)
                    return

                cepen_name = name(chat_id, owner_id)
                if result.status == "scratched":
                    response = (
                        f"{cepen_name} почёсан! Владелец получил 0,1 сита."
                        if cepen_name else "Почёсано! Владелец получил 0,1 сита."
                    )
                    await query.answer(response)
                elif result.status == "user_limit":
                    worm = _genitive_from_name(cepen_name)
                    await query.answer(
                        f"Ты уже почесал {worm} 5 раз сегодня.",
                        show_alert=True,
                    )
                elif result.status == "worm_limit":
                    worm = subject_from_name(cepen_name, capital=True)
                    await query.answer(
                        f"{worm} сегодня уже почесан 50 раз.",
                        show_alert=True,
                    )
                else:
                    await query.answer("Это нажатие уже учтено.")

                _, scratch_count = scratch_summary(chat_id, owner_id)
                try:
                    await query.message.edit_text(
                        status_text(chat_id, owner_id),
                        reply_markup=menu_keyboard(
                            owner_id,
                            length(chat_id, owner_id) > 0,
                            cepen_name,
                            scratch_count,
                        ),
                    )
                except TelegramBadRequest as exc:
                    if "message is not modified" not in str(exc).lower():
                        raise
            return
        if query.from_user.id != owner_id:
            await query.answer(
                "Это меню другого пользователя. Вызови своё с помощью /cepen",
                show_alert=True,
            )
            return
        if action == "name":
            if length(chat_id, owner_id) <= 0:
                await query.answer("У тебя больше нет цепня.", show_alert=True)
                return
            current_name = name(chat_id, owner_id)
            await state.set_state(CepenNameStates.waiting_for_name)
            await state.update_data(cepen_name_chat_id=chat_id, cepen_name_owner_id=owner_id)
            prompt = "Как назвать цепня?"
            if current_name:
                prompt = (
                    f"Сейчас цепня зовут {current_name}. Как назвать его теперь?\n"
                    "Введите - чтобы удалить имя"
                )
            await query.message.answer(prompt)
            await query.answer()
            return
        if action in {"rating", "rating_full"}:
            full = action == "rating_full"
            text, total = ranking_text(chat_id, full=full)
            await query.message.edit_text(
                text,
                reply_markup=rating_keyboard(owner_id, total, full=full),
            )
            await query.answer()
            return
        await query.answer()

    @dp.message(CepenNameStates.waiting_for_name)
    async def cepen_name_input(message, state: FSMContext):
        data = await state.get_data()
        chat_id = data.get("cepen_name_chat_id")
        owner_id = data.get("cepen_name_owner_id")
        if chat_id != message.chat.id or owner_id != message.from_user.id:
            return
        action, normalized = normalize_name_input(message.text)
        if action == "empty":
            await message.reply("Имя не может быть пустым. Попробуй ещё раз.")
            return
        if action == "too_long":
            await message.reply(
                f"Слишком длинное имя. Нужно не больше {CEPEN_NAME_MAX_LENGTH} символов."
            )
            return
        result = set_name(chat_id, owner_id, normalized)
        if result == "disabled":
            await state.clear()
            await message.reply("Цепень отключён в этом чате.")
            return
        if result == "healthy":
            await state.clear()
            await message.reply("У тебя больше нет цепня.")
            return
        await state.clear()
        if result == "deleted":
            await message.reply("Имя цепня удалено.")
        else:
            await message.reply(f"Теперь твоего цепня зовут {normalized}!")

    @dp.message(Command("cure"))
    async def cure_command(message):
        if message.from_user.id != DOCTOR_ID:
            await message.reply("Ты не дядя доктор! В меде отучить 67 лет, потом попробуй ещё раз")
            return
        parts = (message.text or "").split()
        target = _target_by_nick(message.chat.id, parts[1]) if len(parts) == 2 else None
        if target is None:
            await message.reply("Укажи известного участника: /cure @nickname")
            return
        cepen_name = name(message.chat.id, target)
        result = cure(message.chat.id, target)
        if result == "cured":
            worm = subject_from_name(cepen_name, capital=True, html_mode=True)
            await message.reply(
                f"{worm} у {mention(message.chat.id, target)} исцелён!",
                parse_mode="HTML",
            )
        elif result == "disabled":
            await message.reply("Цепень отключён в этом чате.")
        else:
            await message.reply("У этого участника нет цепня.")
