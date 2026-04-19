import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from db import get_connection


CODE_TTL_SECONDS = 2 * 60 * 60
_MAX_ATTEMPTS = 100


class AuthCodeError(Exception):
    pass


class AuthCodeInvalidError(AuthCodeError):
    pass


class AuthCodeExpiredError(AuthCodeError):
    pass


class AuthCodeUsedError(AuthCodeError):
    pass


class AuthCodeConflictError(AuthCodeError):
    pass


@dataclass
class IssuedAuthCode:
    user_id: int
    code: str
    expires_at: int


def _secret_bytes() -> bytes:
    secret = (
        os.getenv("WEB_AUTH_CODE_SECRET", "").strip()
        or os.getenv("BOT_TOKEN", "").strip()
        or "udb-fallback-auth-code-secret"
    )
    return secret.encode("utf-8")


def _utc_hour_bucket(ts: int) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y%m%d%H")


def _generate_code(user_id: int, bucket: str, attempt: int) -> str:
    payload = f"{user_id}:{bucket}:{attempt}"
    digest = hmac.new(_secret_bytes(), payload.encode("utf-8"), hashlib.sha256).digest()
    value = int.from_bytes(digest[:4], byteorder="big") % 10000
    return f"{value:04d}"


def _ensure_table() -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS web_auth_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                issued_bucket TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                expires_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                used_at INTEGER
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_web_auth_codes_code ON web_auth_codes(code)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_web_auth_codes_user ON web_auth_codes(user_id)"
        )
        conn.commit()


def _cleanup(now_ts: int) -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        # Keep a small history window for diagnostics and old in-flight codes.
        cur.execute(
            "DELETE FROM web_auth_codes WHERE expires_at < ? AND (used_at IS NULL OR used_at < ?)",
            (now_ts - 24 * 60 * 60, now_ts - 24 * 60 * 60),
        )
        conn.commit()


def issue_auth_code(user_id: int) -> IssuedAuthCode:
    now_ts = int(time.time())
    bucket = _utc_hour_bucket(now_ts)
    _ensure_table()
    _cleanup(now_ts)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT code, expires_at
            FROM web_auth_codes
            WHERE user_id = ? AND issued_bucket = ? AND used_at IS NULL AND expires_at >= ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id, bucket, now_ts),
        )
        row = cur.fetchone()
        if row:
            return IssuedAuthCode(user_id=user_id, code=str(row["code"]), expires_at=int(row["expires_at"]))

        code = None
        attempt_used = None
        for attempt in range(_MAX_ATTEMPTS):
            candidate = _generate_code(user_id, bucket, attempt)
            cur.execute(
                """
                SELECT 1
                FROM web_auth_codes
                WHERE code = ? AND used_at IS NULL AND expires_at >= ? AND user_id != ?
                LIMIT 1
                """,
                (candidate, now_ts, user_id),
            )
            if cur.fetchone() is None:
                code = candidate
                attempt_used = attempt
                break

        if code is None or attempt_used is None:
            raise AuthCodeConflictError("Не удалось выдать код. Попробуйте позже.")

        # Invalidate previous active codes for this user.
        cur.execute(
            """
            UPDATE web_auth_codes
            SET used_at = ?
            WHERE user_id = ? AND used_at IS NULL
            """,
            (now_ts, user_id),
        )

        expires_at = now_ts + CODE_TTL_SECONDS
        cur.execute(
            """
            INSERT INTO web_auth_codes (user_id, code, issued_bucket, attempt, expires_at, created_at, used_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL)
            """,
            (user_id, code, bucket, attempt_used, expires_at, now_ts),
        )
        conn.commit()

    return IssuedAuthCode(user_id=user_id, code=code, expires_at=expires_at)


def consume_auth_code(code: str) -> int:
    normalized = "".join(ch for ch in str(code) if ch.isdigit())
    if len(normalized) != 4:
        raise AuthCodeInvalidError("Код должен состоять из 4 цифр.")

    now_ts = int(time.time())
    _ensure_table()
    _cleanup(now_ts)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, user_id, code, issued_bucket, attempt, expires_at, used_at, created_at
            FROM web_auth_codes
            WHERE code = ?
            ORDER BY created_at DESC
            LIMIT 5
            """,
            (normalized,),
        )
        rows = cur.fetchall()

        if not rows:
            raise AuthCodeInvalidError("Неверный код.")

        active_rows = [r for r in rows if r["used_at"] is None and int(r["expires_at"]) >= now_ts]
        if len(active_rows) > 1:
            raise AuthCodeConflictError("Код неоднозначен. Запросите новый код командой /auth.")
        if len(active_rows) == 0:
            latest = rows[0]
            if latest["used_at"] is not None:
                raise AuthCodeUsedError("Код уже использован. Запросите новый командой /auth.")
            raise AuthCodeExpiredError("Код устарел. Запросите новый командой /auth.")

        row = active_rows[0]
        expected = _generate_code(int(row["user_id"]), str(row["issued_bucket"]), int(row["attempt"]))
        if expected != normalized:
            cur.execute("UPDATE web_auth_codes SET used_at = ? WHERE id = ? AND used_at IS NULL", (now_ts, int(row["id"])))
            conn.commit()
            raise AuthCodeInvalidError("Неверный код.")

        cur.execute(
            """
            UPDATE web_auth_codes
            SET used_at = ?
            WHERE id = ? AND used_at IS NULL
            """,
            (now_ts, int(row["id"])),
        )
        if cur.rowcount == 0:
            conn.commit()
            raise AuthCodeUsedError("Код уже использован. Запросите новый командой /auth.")
        conn.commit()
        return int(row["user_id"])
