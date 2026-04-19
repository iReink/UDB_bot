import base64
import hashlib
import hmac
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from auth_code import (
    AuthCodeConflictError,
    AuthCodeExpiredError,
    AuthCodeInvalidError,
    AuthCodeUsedError,
    consume_auth_code,
)


BASE_DIR = Path(__file__).resolve().parents[1]
DB_FILE = BASE_DIR / "stats.db"
WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

COOKIE_NAME = "udb_web_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30
TELEGRAM_AUTH_MAX_AGE_SECONDS = 60 * 60 * 24

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip()
SESSION_SECRET = os.getenv("WEB_SESSION_SECRET", "").strip()

if not SESSION_SECRET and BOT_TOKEN:
    SESSION_SECRET = hashlib.sha256(f"{BOT_TOKEN}:web-session".encode("utf-8")).hexdigest()

app = FastAPI(title="UDB Web")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


class TelegramAuthRequest(BaseModel):
    auth_data: dict[str, Any] = Field(default_factory=dict)


class SelectChatRequest(BaseModel):
    chat_id: int


class CodeAuthRequest(BaseModel):
    code: str = ""


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def _chat_label(user_id: int, chat_id: int) -> str:
    if user_id == chat_id:
        return "ЛС"
    return f"Чат {chat_id}"


def _get_user_accounts(user_id: int) -> list[dict[str, Any]]:
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT user_id, chat_id, COALESCE(name, '') AS name, COALESCE(sits, 0) AS sits
            FROM users
            WHERE user_id = ?
            ORDER BY CASE WHEN chat_id = user_id THEN 0 ELSE 1 END, chat_id
            """,
            (user_id,),
        )
        rows = cur.fetchall()

    accounts: list[dict[str, Any]] = []
    for row in rows:
        chat_id = int(row["chat_id"])
        accounts.append(
            {
                "chat_id": chat_id,
                "label": _chat_label(int(row["user_id"]), chat_id),
                "name": row["name"] or "",
                "balance": int(row["sits"] or 0),
            }
        )
    return accounts


def _get_user_name_for_session(user_id: int) -> str:
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COALESCE(name, '') AS name
            FROM users
            WHERE user_id = ? AND COALESCE(name, '') <> ''
            ORDER BY CASE WHEN chat_id = user_id THEN 0 ELSE 1 END, chat_id
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
    return row["name"] if row else ""


def _build_data_check_string(auth_data: dict[str, Any]) -> str:
    pairs: list[str] = []
    for key in sorted(auth_data.keys()):
        if key == "hash":
            continue
        value = auth_data.get(key)
        if value is None:
            continue
        pairs.append(f"{key}={value}")
    return "\n".join(pairs)


def _verify_telegram_auth(auth_data: dict[str, Any]) -> bool:
    if not BOT_TOKEN:
        return False
    recv_hash = str(auth_data.get("hash", ""))
    if not recv_hash:
        return False
    auth_date_raw = auth_data.get("auth_date")
    try:
        auth_date = int(auth_date_raw)
    except (TypeError, ValueError):
        return False

    if int(time.time()) - auth_date > TELEGRAM_AUTH_MAX_AGE_SECONDS:
        return False

    data_check_string = _build_data_check_string(auth_data)
    secret_key = hashlib.sha256(BOT_TOKEN.encode("utf-8")).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(calc_hash, recv_hash)


def _b64_url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64_url_decode(data: str) -> bytes:
    padding = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign_payload(payload: dict[str, Any]) -> str:
    if not SESSION_SECRET:
        raise RuntimeError("WEB_SESSION_SECRET (or BOT_TOKEN) is required for session signing")
    payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload_b64 = _b64_url_encode(payload_json)
    signature = hmac.new(SESSION_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"


def _read_payload(token: str | None) -> dict[str, Any] | None:
    if not token or "." not in token or not SESSION_SECRET:
        return None
    payload_b64, signature = token.split(".", 1)
    expected = hmac.new(SESSION_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        payload_json = _b64_url_decode(payload_b64).decode("utf-8")
        payload = json.loads(payload_json)
        if not isinstance(payload, dict):
            return None
        return payload
    except Exception:
        return None


def _set_session_cookie(response: JSONResponse, payload: dict[str, Any]) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=_sign_payload(payload),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )


def _clear_session_cookie(response: JSONResponse) -> None:
    response.delete_cookie(COOKIE_NAME)


def _require_session(request: Request) -> dict[str, Any]:
    payload = _read_payload(request.cookies.get(COOKIE_NAME))
    if not payload:
        raise HTTPException(status_code=401, detail="Not authorized")
    return payload


def _prepare_state(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    user_id = int(payload["telegram_user_id"])
    accounts = _get_user_accounts(user_id)
    chat_ids = {item["chat_id"] for item in accounts}

    selected_chat_id = payload.get("selected_chat_id")
    if selected_chat_id not in chat_ids:
        selected_chat_id = None

    selected = None
    if selected_chat_id is not None:
        for item in accounts:
            if item["chat_id"] == selected_chat_id:
                selected = item
                break

    state = {
        "authorized": True,
        "bot_username": BOT_USERNAME,
        "user": {
            "id": user_id,
            "first_name": payload.get("first_name", ""),
            "last_name": payload.get("last_name", ""),
            "username": payload.get("username", ""),
        },
        "chats": [{"chat_id": item["chat_id"], "label": item["label"]} for item in accounts],
        "selected_chat_id": selected["chat_id"] if selected else None,
        "selected_chat_label": selected["label"] if selected else None,
        "balance": selected["balance"] if selected else None,
    }

    next_payload = dict(payload)
    next_payload["selected_chat_id"] = selected_chat_id
    return state, next_payload


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"bot_username": BOT_USERNAME},
    )


@app.get("/api/state")
def get_state(request: Request) -> JSONResponse:
    payload = _read_payload(request.cookies.get(COOKIE_NAME))
    if not payload:
        return JSONResponse({"authorized": False, "bot_username": BOT_USERNAME})

    state, next_payload = _prepare_state(payload)
    response = JSONResponse(state)
    _set_session_cookie(response, next_payload)
    return response


@app.post("/api/auth/telegram")
def auth_telegram(data: TelegramAuthRequest) -> JSONResponse:
    auth_data = data.auth_data
    if not _verify_telegram_auth(auth_data):
        raise HTTPException(status_code=401, detail="Invalid Telegram auth data")

    try:
        telegram_user_id = int(auth_data["id"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Telegram user id is missing")

    payload: dict[str, Any] = {
        "telegram_user_id": telegram_user_id,
        "first_name": str(auth_data.get("first_name", "")),
        "last_name": str(auth_data.get("last_name", "")),
        "username": str(auth_data.get("username", "")),
        "selected_chat_id": None,
    }

    state, next_payload = _prepare_state(payload)
    response = JSONResponse(state)
    _set_session_cookie(response, next_payload)
    return response


@app.post("/api/auth/code")
def auth_by_code(data: CodeAuthRequest) -> JSONResponse:
    try:
        user_id = consume_auth_code(data.code)
    except AuthCodeInvalidError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except AuthCodeExpiredError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except AuthCodeUsedError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except AuthCodeConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    display_name = _get_user_name_for_session(user_id)
    payload: dict[str, Any] = {
        "telegram_user_id": user_id,
        "first_name": display_name,
        "last_name": "",
        "username": "",
        "selected_chat_id": None,
    }

    state, next_payload = _prepare_state(payload)
    response = JSONResponse(state)
    _set_session_cookie(response, next_payload)
    return response


@app.post("/api/select-chat")
def select_chat(request: Request, data: SelectChatRequest) -> JSONResponse:
    payload = _require_session(request)
    user_id = int(payload["telegram_user_id"])
    accounts = _get_user_accounts(user_id)
    allowed_chat_ids = {item["chat_id"] for item in accounts}
    if data.chat_id not in allowed_chat_ids:
        raise HTTPException(status_code=403, detail="Chat is not available for this user")

    payload = dict(payload)
    payload["selected_chat_id"] = data.chat_id
    state, next_payload = _prepare_state(payload)
    response = JSONResponse(state)
    _set_session_cookie(response, next_payload)
    return response


@app.post("/api/logout")
def logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    _clear_session_cookie(response)
    return response


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
