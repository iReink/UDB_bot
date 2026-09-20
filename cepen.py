"""Shared tapeworm state and Telegram entry points."""

import asyncio
import html
import logging
import random
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from aiogram.filters import Command

import db
from sits import format_sits


DOCTOR_ID = 1235654176
CURE_PRICE = 50
INITIAL_LENGTH = 5.0
INSTRUCTION = (
    "Цепень будет есть твой сит и расти. Его может вылечить официальный дядя "
    "доктор @jprgprh или коммерческий доктор в магазине /shop"
)
PRIMARY_CHANCES = {"geyser": .025, "coffee": .004, "round": .003, "sticker": .0006}
PAIR_CHANCES = {
    "sos": (.90, .10),
    "shpeh": (.98, .12),
    "bite": (.95, .03),
    "duel": (.20, .20),
}
GROUP_CHANCES = {"group_participant": .15, "group_spectator": .05, "daily": .15}
REPLY_CHANCE = .005
_THOUSANDTH = Decimal("0.001")

GROWTH_LINES = (
    "{name}: цепень сходил в /shop за питанием — {length} см (+{gain}), −{cost} сит.",
    "{name}: цепень провёл дейлик внутри хозяина — {length} см (+{gain}), −{cost} сит.",
    "{name}: маленький гейзер в животе дал +{gain} см. Теперь {length} см, −{cost} сит.",
    "{name}: цепень поймал внутренний сит и вытянулся до {length} см (+{gain}), −{cost} сит.",
    "{name}: вместо кофе цепень выпил сит — {length} см (+{gain}), −{cost} сит.",
    "{name}: за ночь цепень нафармил +{gain} см. Длина {length} см, цена {cost} сит.",
    "{name}: цепень победил в споре с желудком — {length} см (+{gain}), −{cost} сит.",
    "{name}: цепень посмотрел кружочек и стал длиннее: {length} см (+{gain}), −{cost} сит.",
    "{name}: под внутренним стикером нашлось +{gain} см. Теперь {length} см, −{cost} сит.",
    "{name}: цепень оформил подписку на рост — {length} см (+{gain}), −{cost} сит.",
    "{name}: чат флудил, цепень не отставал — {length} см (+{gain}), −{cost} сит.",
    "{name}: цепень занял первое место в рейтинге самого себя: {length} см (+{gain}), −{cost} сит.",
    "{name}: пока все считали сообщения, цепень насчитал {length} см (+{gain}), −{cost} сит.",
    "{name}: цепень заглянул к дяде доктору, но выбрал буфет — {length} см (+{gain}), −{cost} сит.",
    "{name}: цепень сделал зарядку в форме буквы С — {length} см (+{gain}), −{cost} сит.",
    "{name}: цепень проснулся раньше гейзера — {length} см (+{gain}), −{cost} сит.",
    "{name}: цепень обновил личный рекорд: {length} см (+{gain}), −{cost} сит.",
    "{name}: цепень получил сит за вредное дело — {length} см (+{gain}), −{cost} сит.",
    "{name}: у цепня сегодня успешный дейлик роста: {length} см (+{gain}), −{cost} сит.",
    "{name}: цепень выиграл битву за место в животе — {length} см (+{gain}), −{cost} сит.",
)


def _round_length(value: Decimal) -> float:
    return float(value.quantize(_THOUSANDTH, rounding=ROUND_HALF_UP))


def growth_preview(length: float) -> tuple[float, float, float]:
    old = Decimal(str(length))
    new = _round_length(old * Decimal("1.15"))
    cost = _round_length(old * Decimal("0.1"))
    return new, _round_length(Decimal(str(new)) - old), cost


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


def _infect(conn, chat_id: int, user_id: int) -> bool:
    created = conn.execute(
        "INSERT OR IGNORE INTO users(user_id,chat_id,name,cepen) VALUES (?,?,?,?)",
        (user_id, chat_id, str(user_id), INITIAL_LENGTH),
    )
    if created.rowcount == 1:
        return True
    cur = conn.execute(
        "UPDATE users SET cepen=? WHERE chat_id=? AND user_id=? AND COALESCE(cepen,0)=0",
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
    phrases = {
        "group_participant": f"{ill} подрочил вместе с заражённым {carrier} и подхватил цепня",
        "group_spectator": f"{ill} подглядывал за заражённым {carrier} и подхватил цепня",
        "duel": f"{ill} так сильно скрестил шпагу, что цепень {carrier} переполз по мостику",
        "daily": f"{ill} присел на дейлике рядом с {carrier} и получил цепня",
        "reply": f"Ответив на сообщение, {ill} коснулся цепня {carrier} и теперь цепня два.",
    }
    if kind == "sos":
        phrase = (f"{carrier} всосал в {ill} цепня" if source_initiated
                  else f"{ill} засосал {carrier} и высосал себе цепня")
    elif kind == "shpeh":
        phrase = (f"{carrier} пошпёхал {ill} и подарил цепня" if source_initiated
                  else f"{ill} пошпёхался с {carrier} и получил хеппиэндинг-цепня")
    elif kind == "bite":
        phrase = (f"{carrier} передал цепня {ill} через укус. Вампир хренов."
                  if source_initiated else f"{ill} откусил у {carrier} кусочек цепня")
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
        conn.execute("UPDATE users SET cepen=0,cepen_growth_date=NULL WHERE chat_id=? AND user_id=?",
                     (chat_id, user_id))
        conn.commit()
        return "cured"


def status_text(chat_id: int, user_id: int) -> str:
    with closing(db.get_connection()) as conn:
        if not db.cepen_enabled(chat_id, conn):
            return "Цепень отключён в этом чате."
        row = conn.execute("SELECT cepen,sits FROM users WHERE chat_id=? AND user_id=?",
                           (chat_id, user_id)).fetchone()
    if not row or float(row["cepen"] or 0) <= 0:
        return "Цепня пока нет. Береги сит."
    old = float(row["cepen"])
    _, gain, cost = growth_preview(old)
    return (f"🪱 Длина цепня: {format_sits(old)} см\n"
            f"Сегодня вечером прирост: {format_sits(gain)} см\n"
            f"Стоимость: {format_sits(cost)} сит (баланс: {format_sits(row['sits'] or 0)} сит)")


def grow_all(date_key: str) -> dict[int, list[str]]:
    reports: dict[int, list[str]] = {}
    with closing(db.get_connection()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT user_id,chat_id,cepen,sits FROM users WHERE cepen>0 AND chat_id<0 "
            "AND COALESCE(cepen_growth_date,'')<>? ORDER BY chat_id,user_id", (date_key,)
        ).fetchall()
        for row in rows:
            chat_id, user_id = int(row["chat_id"]), int(row["user_id"])
            if not db.cepen_enabled(chat_id, conn):
                continue
            old = float(row["cepen"])
            new, gain, cost = growth_preview(old)
            name = _mention(conn, chat_id, user_id)
            if float(row["sits"] or 0) + 1e-9 >= cost:
                db.apply_sit_change(conn, chat_id, user_id, -cost,
                                    action_code="cepen_growth", action_ru="Рост цепня",
                                    metadata={"growth_date": date_key, "old_cm": old, "new_cm": new},
                                    require_sufficient=True)
                conn.execute("UPDATE users SET cepen=?,cepen_growth_date=? WHERE chat_id=? AND user_id=?",
                             (new, date_key, chat_id, user_id))
                line = random.choice(GROWTH_LINES).format(
                    name=name, length=format_sits(new), gain=format_sits(gain), cost=format_sits(cost))
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
                line = f"У {name} недостаточно сит для роста, поэтому цепень в анабиозе."
                if count:
                    line += " Для поддержания жизнедеятельности цепень съел половину сообщений."
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
                await bot.send_message(chat_id, "🪱 Цепни подвели итоги дня:\n" + "\n".join(lines),
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
        await message.reply(status_text(message.chat.id, message.from_user.id))

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
        result = cure(message.chat.id, target)
        if result == "cured":
            await message.reply(f"Цепень {mention(message.chat.id, target)} исцелён!", parse_mode="HTML")
        elif result == "disabled":
            await message.reply("Цепень отключён в этом чате.")
        else:
            await message.reply("У этого участника нет цепня.")
