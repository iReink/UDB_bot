"""Cached FLUX portraits for individual tapeworms (cepen)."""
from __future__ import annotations

import json
import os
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

import db
from schema_once import once as schema_once
import cepen_avatar

MODEL = "@cf/black-forest-labs/flux-2-klein-4b"
DAILY_LIMIT = 5
COST = 5
AVATAR_REVISION = "reference-realism-v5"
ROOT = Path(os.getenv("CEPEN_AI_AVATAR_DIR", Path(__file__).resolve().parent / "cepen_ai_avatar_cache"))
_IN_FLIGHT: set[tuple[int, int, int, str]] = set()


def in_flight(chat_id: int, user_id: int, level: int, skin: str) -> bool:
    return (chat_id, user_id, level, skin or "") in _IN_FLIGHT


def mark_in_flight(chat_id: int, user_id: int, level: int, skin: str) -> bool:
    key=(chat_id,user_id,level,skin or "")
    if key in _IN_FLIGHT:
        return False
    _IN_FLIGHT.add(key)
    return True


def clear_in_flight(chat_id: int, user_id: int, level: int, skin: str) -> None:
    _IN_FLIGHT.discard((chat_id,user_id,level,skin or ""))


def _day() -> str:
    return datetime.now(timezone(timedelta(hours=5))).date().isoformat()


@schema_once(lambda: db.DB_FILE)
def ensure_schema(conn=None) -> None:
    own = conn is None
    if own:
        conn = db.get_connection()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS cepen_ai_avatars(
          chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
          level INTEGER NOT NULL, skin TEXT NOT NULL DEFAULT '',
          path TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
          generated_at TEXT NOT NULL, PRIMARY KEY(chat_id,user_id));
        CREATE TABLE IF NOT EXISTS cepen_ai_avatar_usage(
          chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, day TEXT NOT NULL,
          count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(chat_id,user_id,day));
        """)
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


def _state(chat_id: int, user_id: int):
    with closing(db.get_connection()) as conn:
        ensure_schema(conn)
        return conn.execute("SELECT * FROM cepen_ai_avatars WHERE chat_id=? AND user_id=?", (chat_id, user_id)).fetchone()


def is_enabled(chat_id: int, user_id: int) -> bool:
    row = _state(chat_id, user_id)
    return not row or bool(row["enabled"])


def set_enabled(chat_id: int, user_id: int, enabled: bool) -> None:
    with closing(db.get_connection()) as conn, conn:
        ensure_schema(conn)
        row = conn.execute("SELECT path,level,skin,generated_at FROM cepen_ai_avatars WHERE chat_id=? AND user_id=?", (chat_id,user_id)).fetchone()
        if row:
            conn.execute("UPDATE cepen_ai_avatars SET enabled=? WHERE chat_id=? AND user_id=?", (int(enabled),chat_id,user_id))
        else:
            conn.execute("INSERT INTO cepen_ai_avatars(chat_id,user_id,level,skin,path,enabled,generated_at) VALUES(?,?,?,?,?,?,?)", (chat_id,user_id,0,"", "",int(enabled),datetime.now(timezone.utc).isoformat()))


def cached(chat_id: int, user_id: int, level: int, skin: str) -> str | None:
    row = _state(chat_id, user_id)
    if not row or not row["enabled"] or row["level"] != level or row["skin"] != (skin or ""):
        return None
    path = Path(row["path"])
    if not path.name.startswith(AVATAR_REVISION + "_"):
        return None
    return str(path) if path.is_file() else None


def usage(chat_id: int, user_id: int) -> int:
    with closing(db.get_connection()) as conn:
        ensure_schema(conn)
        row=conn.execute("SELECT count FROM cepen_ai_avatar_usage WHERE chat_id=? AND user_id=? AND day=?",(chat_id,user_id,_day())).fetchone()
        return int(row[0]) if row else 0


def _profile(chat_id: int, user_id: int) -> str:
    try:
        import ai_tasks
        raw = ai_tasks.get_latest_profile_json(user_id, chat_id)
        if raw:
            data=json.loads(raw)
            fields=("short_summary","communication_style","behavior_notes")
            chunks=[]
            for key in fields:
                value=data.get(key)
                if isinstance(value,list): value=", ".join(str(x) for x in value[:5])
                if value: chunks.append(f"{key}: {value}")
            return "\n".join(chunks)[:400]
    except Exception:
        pass
    return ""


def prompt(chat_id: int, user_id: int, skin: str, level: int) -> str:
    personality=_profile(chat_id,user_id)
    return ("Re-render the cartoon tapeworm in input_image_0 using photorealistic materials and lighting. "
            "Preserve the original character design: its elongated segmented body, colors, clothing, accessories and exact facial geometry. "
            "Its face has eyes and a mouth, with a continuous flat surface between them, including beneath the glasses. "
            "Keep these simplified cartoon features while making their surfaces look physically real. "
            "Convey the owner's personality through a subtle cheerful expression, keeping the character always cute and slightly fluffy. "
            "Preserve the reference pose, background and framing.\n"
            f"Owner personality: {personality or 'friendly'}")


def _reserve_paid(conn, chat_id: int, user_id: int) -> bool:
    row = conn.execute("SELECT count FROM cepen_ai_avatar_usage WHERE chat_id=? AND user_id=? AND day=?", (chat_id, user_id, _day())).fetchone()
    if row and int(row[0]) >= DAILY_LIMIT:
        return False
    conn.execute("INSERT INTO cepen_ai_avatar_usage VALUES(?,?,?,1) ON CONFLICT(chat_id,user_id,day) DO UPDATE SET count=count+1", (chat_id,user_id,_day()))
    return True


def reserve(chat_id: int, user_id: int) -> bool:
    with closing(db.get_connection()) as conn, conn:
        ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        return _reserve_paid(conn, chat_id, user_id)


def purchase_regeneration(chat_id: int, user_id: int) -> None:
    """Reserve one paid attempt and debit its cost in the same transaction."""
    ensure_schema()
    with closing(db.get_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _reserve_paid(conn, chat_id, user_id):
            raise RuntimeError("Лимит платных перегенераций на сегодня исчерпан (5).")
        db.apply_sit_change(conn, chat_id, user_id, -COST,
                            action_code="cepen_ai_avatar_regeneration",
                            action_ru="Перегенерация ИИ-аватарки цепня",
                            metadata={"model": "flux-2-klein-4b"}, require_sufficient=True)


def generate(chat_id: int, user_id: int, level: int, skin: str, source: str) -> str:
    from imagegen import generate_cloudflare, validate_image
    import ai_tasks
    ai_tasks.ensure_ai_tasks_table()
    now=datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0).isoformat()
    with closing(db.get_connection()) as conn, conn:
        task_id=conn.execute("INSERT INTO ai_tasks(task_type,status,priority,model,prompt,payload_json,chat_id,user_id,request_message_id,created_at,updated_at) VALUES('cepen_avatar','processing',250,?,?,?,?,?,?,?,?)",(MODEL,"",json.dumps({"level":level,"skin":skin}),chat_id,user_id,0,now,now)).lastrowid
        task={"id":task_id,"user_id":user_id}
    raw=Image.open(source).convert("RGB")
    import io
    stream=io.BytesIO(); raw.save(stream,"JPEG",quality=90); raw.close()
    result=generate_cloudflare(task,prompt(chat_id,user_id,skin,level),[stream.getvalue()],(1024,1024))
    result=validate_image(result)
    ROOT.mkdir(parents=True,exist_ok=True)
    target=ROOT/f"{AVATAR_REVISION}_{chat_id}_{user_id}_{level}_{skin or 'default'}.png"
    target.write_bytes(result)
    with closing(db.get_connection()) as conn, conn:
        ensure_schema(conn)
        conn.execute("INSERT INTO cepen_ai_avatars VALUES(?,?,?,?,?,?,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET level=excluded.level,skin=excluded.skin,path=excluded.path,enabled=1,generated_at=excluded.generated_at",(chat_id,user_id,level,skin or "",str(target),1,datetime.now(timezone.utc).isoformat()))
        conn.execute("UPDATE ai_tasks SET status='done',model=?,updated_at=?,finished_at=? WHERE id=?",(MODEL,now,now,task_id))
    return str(target)
