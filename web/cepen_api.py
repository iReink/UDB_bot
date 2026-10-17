"""Owner-scoped external cepen API; no dependency on web visitor state."""
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
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from auth_code import (AuthCodeInvalidError, AuthCodeExpiredError,
                       AuthCodeUsedError, AuthCodeConflictError)

PREFIX = '/api/v1'
TOKEN_TTL = 30 * 24 * 60 * 60
SCOPE = 'cepen:read'
WRITE_SCOPE = 'cepen:scratch'
TZ = ZoneInfo('Asia/Yekaterinburg')


class Login(BaseModel):
    code: str = Field(min_length=4, max_length=4, pattern=r'^[0-9]{4}$')

    class Config:
        extra = 'forbid'


class ScratchRequest(BaseModel):
    chat_id: int = Field(strict=True, lt=0, ge=-(2**63))
    count: int = Field(strict=True, ge=1, le=50)
    request_id: UUID

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
    payload = {'sub': int(user), 'exp': exp, 'scope': f'{SCOPE} {WRITE_SCOPE}', 'v': 1,
               'nonce': secrets.token_urlsafe(12)}
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=')
    sig = hmac.new(signing_key(secret), body.encode(), hashlib.sha256).hexdigest()
    return body + '.' + sig, exp


def token_user(request, secret, *, required_scope=None):
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
        if (SCOPE not in str(payload.get('scope', '')).split() or payload.get('v') != 1
                or type(payload.get('sub')) is not int or payload['sub'] <= 0
                or type(payload.get('exp')) is not int or payload['exp'] <= int(time.time())):
            raise ValueError('claims')
        if required_scope and required_scope not in str(payload.get('scope', '')).split():
            fail(403, 'insufficient_scope', 'New code login required for scratching')
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
                             'scope': f'{SCOPE} {WRITE_SCOPE}'}, headers={'Cache-Control': 'no-store'})

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

    @router.post('/cepen/scratch')
    def scratch(data: ScratchRequest, request: Request):
        import cepen
        import db
        from cepen_messages import mood_for_scratches
        user = token_user(request, secret(), required_scope=WRITE_SCOPE)
        key = str(data.request_id)
        with closing(connect()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            row = user_row(conn, user, data.chat_id)
            previous = conn.execute(
                'SELECT requested_count,response_json FROM cepen_api_scratch_requests '
                'WHERE chat_id=? AND user_id=? AND request_id=?',
                (data.chat_id, user, key)).fetchone()
            if previous:
                if previous['requested_count'] != data.count:
                    fail(409, 'idempotency_conflict', 'request_id already used with another count')
                return JSONResponse(json.loads(previous['response_json']), headers={'Cache-Control': 'no-store'})
            if not db.cepen_enabled(data.chat_id, conn):
                fail(409, 'mechanic_disabled', 'Cepen mechanic disabled in this chat')
            if float(row['cepen'] or 0) <= 0:
                fail(404, 'no_cepen', 'No cepen for this account')
            day = datetime.now(TZ).date().isoformat()
            accepted, reward, reason = 0, 0.0, None
            total, personal = cepen._scratch_counts(conn, data.chat_id, user, user, day)
            for index in range(data.count):
                callback = f'api:cepen:{data.chat_id}:{user}:{key}:{index}'
                result = cepen.scratch_in_transaction(conn, data.chat_id, user, user, callback, date_key=day)
                total, personal = result.total_count, result.scratcher_count
                if result.status != 'scratched':
                    reason = result.status
                    break
                accepted += 1
                reward += conn.execute('SELECT reward FROM cepen_scratches WHERE callback_query_id=?', (callback,)).fetchone()[0]
            value = {'request_id': key, 'requested_count': data.count, 'accepted_count': accepted,
                     'rejected_count': data.count - accepted, 'reason': reason, 'scratch_date': day,
                     'total_count': total, 'owner_count': personal,
                     'owner_remaining': max(0, cepen.SCRATCH_USER_DAILY_LIMIT - personal),
                     'total_remaining': max(0, cepen.SCRATCH_DAILY_LIMIT - total),
                     'reward_sits': round(reward, 3), 'mood': mood_for_scratches(total)}
            conn.execute('INSERT INTO cepen_api_scratch_requests '
                         '(chat_id,user_id,request_id,requested_count,response_json,created_at) VALUES(?,?,?,?,?,?)',
                         (data.chat_id, user, key, data.count, json.dumps(value, ensure_ascii=False),
                          datetime.now(timezone.utc).isoformat()))
        return JSONResponse(value, headers={'Cache-Control': 'no-store'})

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
        with open(path, 'rb') as source:
            signature = source.read(12)
        media_type = ('image/jpeg' if signature.startswith(b'\xff\xd8\xff') else
                      'image/webp' if signature.startswith(b'RIFF') and signature[8:12] == b'WEBP' else 'image/png')
        return FileResponse(path, media_type=media_type, headers={'Cache-Control': 'private, max-age=300',
                                                                 'Vary': 'Authorization', 'X-Content-Type-Options': 'nosniff'})

    app.include_router(router)
