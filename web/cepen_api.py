"""Read-only, owner-scoped external cepen API; no dependency on web visitor state."""
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import deque
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from auth_code import (AuthCodeInvalidError, AuthCodeExpiredError,
                       AuthCodeUsedError, AuthCodeConflictError)

PREFIX = '/api/v1'
TOKEN_TTL = 30 * 24 * 60 * 60
SCOPE = 'cepen:read'
TZ = ZoneInfo('Asia/Yekaterinburg')


class Login(BaseModel):
    code: str = Field(min_length=4, max_length=4, pattern=r'^[0-9]{4}$')

    class Config:
        extra = 'forbid'


def fail(status, code, message, headers=None):
    raise HTTPException(status, detail={'code': code, 'message': message}, headers=headers)


def signing_key(secret):
    if not secret:
        fail(503, 'auth_unavailable', 'API authorization is not configured')
    return hmac.new(secret.encode(), b'udb-cepen-api-v1', hashlib.sha256).digest()


def issue_token(user, secret):
    exp = int(time.time()) + TOKEN_TTL
    payload = {'sub': int(user), 'exp': exp, 'scope': SCOPE, 'v': 1,
               'nonce': secrets.token_urlsafe(12)}
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=')
    sig = hmac.new(signing_key(secret), body.encode(), hashlib.sha256).hexdigest()
    return body + '.' + sig, exp


def token_user(request, secret):
    header = request.headers.get('authorization', '')
    if not header.lower().startswith('bearer '):
        fail(401, 'invalid_token', 'Bearer token required', {'WWW-Authenticate': 'Bearer'})
    token = header[7:].strip()
    try:
        if len(token) > 4096:
            raise ValueError('size')
        body, sig = token.split('.')
        expected = hmac.new(signing_key(secret), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise ValueError('signature')
        payload = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
        if (payload.get('scope') != SCOPE or payload.get('v') != 1
                or type(payload.get('sub')) is not int or payload['sub'] <= 0
                or type(payload.get('exp')) is not int or payload['exp'] <= int(time.time())):
            raise ValueError('claims')
        return payload['sub']
    except (ValueError, TypeError, KeyError, UnicodeError, AttributeError):
        fail(401, 'invalid_token', 'Token invalid or expired', {'WWW-Authenticate': 'Bearer'})


class LoginLimits:
    def __init__(self):
        self.lock = threading.Lock()
        self.clients = {}
        self.total = deque()

    def check(self, ip):
        now = time.monotonic()
        with self.lock:
            while self.total and self.total[0] <= now - 60:
                self.total.popleft()
            if ip not in self.clients and len(self.clients) >= 4096:
                self.clients = {k: v for k, v in self.clients.items() if v and v[-1] > now - 60}
                if len(self.clients) >= 4096:
                    fail(429, 'rate_limited', 'Try again later', {'Retry-After': '60'})
            window = self.clients.setdefault(ip, deque())
            while window and window[0] <= now - 60:
                window.popleft()
            if len(window) >= 5 or len(self.total) >= 30:
                fail(429, 'rate_limited', 'Try again later', {'Retry-After': '60'})
            window.append(now)
            self.total.append(now)


def user_row(conn, user, chat):
    row = conn.execute('SELECT name,cepen,cepen_name,cepen_profession FROM users WHERE user_id=? AND chat_id=?', (user, chat)).fetchone()
    if row is None:
        fail(403, 'chat_forbidden', 'Chat unavailable for this account')
    return row


def avatar_choice(conn, user, chat, row):
    import cepen_avatar
    import cepen_ai_avatar
    length = max(0.0, float(row['cepen'] or 0))
    level = cepen_avatar.level_for_length(length)
    skin = cepen_avatar.normalize_profession(row['cepen_profession'])
    cached = None
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cepen_ai_avatars'").fetchone():
        entry = conn.execute('SELECT * FROM cepen_ai_avatars WHERE chat_id=? AND user_id=?', (chat, user)).fetchone()
        cached = cepen_ai_avatar.cached_path(entry, level, skin)
    if cached:
        path = Path(cached).resolve()
        if path.is_relative_to(cepen_ai_avatar.ROOT.resolve()):
            stat = path.stat()
            version = f'ai:{path.name}:{stat.st_mtime_ns}:{stat.st_size}'
            return path, hashlib.sha256(version.encode()).hexdigest()[:20]
    version = f'cartoon:{level}:{skin or "base"}'
    return None, hashlib.sha256(version.encode()).hexdigest()[:20]


def register_cepen_api(app, *, connect, consume_code, secret, chat_label):
    router = APIRouter(prefix=PREFIX, tags=['Cepen external API'])
    limits = LoginLimits()

    @router.post('/auth/code')
    def login(data: Login, request: Request):
        limits.check(request.client.host if request.client else 'unknown')
        signing_key(secret())  # Check configuration before consuming a one-time code.
        try:
            user = consume_code(data.code)
        except (AuthCodeInvalidError, AuthCodeExpiredError, AuthCodeUsedError) as exc:
            fail(401, 'invalid_code', str(exc))
        except AuthCodeConflictError as exc:
            fail(409, 'ambiguous_code', str(exc))
        token, exp = issue_token(user, secret())
        return JSONResponse({'access_token': token, 'token_type': 'Bearer',
                             'expires_in': TOKEN_TTL,
                             'expires_at': datetime.fromtimestamp(exp, timezone.utc).isoformat(),
                             'scope': SCOPE}, headers={'Cache-Control': 'no-store'})

    @router.get('/chats')
    def chats(request: Request):
        user = token_user(request, secret())
        with closing(connect()) as conn:
            rows = conn.execute('SELECT chat_id FROM users WHERE user_id=? AND chat_id<0 ORDER BY chat_id', (user,)).fetchall()
        return JSONResponse({'chats': [{'chat_id': r['chat_id'], 'chat_name': chat_label(user, r['chat_id'])} for r in rows]}, headers={'Cache-Control': 'no-store'})

    @router.get('/cepen')
    def state(request: Request, chat_id: int = Query(..., lt=0, ge=-(2**63))):
        import db
        import cepen_avatar
        from cepen_messages import mood_for_scratches
        user = token_user(request, secret())
        with closing(connect()) as conn:
            row = user_row(conn, user, chat_id)
            length = max(0.0, float(row['cepen'] or 0))
            exists = length > 0
            profession = cepen_avatar.normalize_profession(row['cepen_profession']) if exists else None
            count = 0
            if exists:
                count = conn.execute('SELECT COUNT(*) FROM cepen_scratches WHERE chat_id=? AND owner_id=? AND scratch_date=?',
                                     (chat_id, user, datetime.now(TZ).date().isoformat())).fetchone()[0]
                _, version = avatar_choice(conn, user, chat_id, row)
            result = {'schema_version': 1, 'user_id': user, 'owner_name': row['name'] or '',
                      'chat_id': chat_id, 'chat_name': chat_label(user, chat_id),
                      'mechanic_enabled': db.cepen_enabled(chat_id, conn),
                      'has_cepen': exists, 'name': (row['cepen_name'] or None) if exists else None,
                      'length_cm': length,
                      'appearance_level': cepen_avatar.level_for_length(length) if exists else None,
                      'profession_id': profession,
                      'profession_title': cepen_avatar.profession_title(profession) if exists else None,
                      'profession_selected': profession is not None,
                      'avatar_url': f'{PREFIX}/cepen/avatar?chat_id={chat_id}&v={version}' if exists else None,
                      'mood': mood_for_scratches(count) if exists else None}
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})

    @router.get('/cepen/avatar')
    def avatar(request: Request, chat_id: int = Query(..., lt=0, ge=-(2**63))):
        import cepen_avatar
        user = token_user(request, secret())
        with closing(connect()) as conn:
            row = user_row(conn, user, chat_id)
            if float(row['cepen'] or 0) <= 0:
                fail(404, 'no_cepen', 'No cepen for this account')
            path, _ = avatar_choice(conn, user, chat_id, row)
        if path is None:
            try:
                path = cepen_avatar.render_avatar(float(row['cepen']), row['cepen_profession'])
            except (OSError, ValueError):
                fail(503, 'avatar_unavailable', 'Avatar temporarily unavailable')
        return FileResponse(path, media_type='image/png', headers={'Cache-Control': 'private, max-age=300',
                                                                 'Vary': 'Authorization', 'X-Content-Type-Options': 'nosniff'})

    app.include_router(router)
