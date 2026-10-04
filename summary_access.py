"""Scoped summary links and Telegram Mini App authentication; no web session issued."""
import base64
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

LINK_TTL = 7 * 86400
INIT_TTL = 86400


class SummaryAccessError(ValueError):
    pass


def link_key(bot_token):
    if not bot_token:
        raise SummaryAccessError('Сервис временно недоступен')
    return hmac.new(bot_token.encode(), b'UDB summary link v1', hashlib.sha256).digest()


def issue_link(chat_id, bot_token, now=None):
    now = int(time.time()) if now is None else now
    raw = json.dumps({'chat':int(chat_id),'exp':now+LINK_TTL},separators=(',',':')).encode()
    payload = base64.urlsafe_b64encode(raw).decode().rstrip('=')
    mac = hmac.new(link_key(bot_token),payload.encode(),hashlib.sha256).hexdigest()[:32]
    return 's1_'+payload+'_'+mac


def read_link(token, bot_token, now=None):
    now = int(time.time()) if now is None else now
    try:
        if not token.startswith('s1_') or len(token)>512:
            raise ValueError()
        payload,mac=token[3:].rsplit('_',1)
        expected=hmac.new(link_key(bot_token),payload.encode(),hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(mac,expected):
            raise ValueError()
        data=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
        if data['exp']<=now or data['exp']>now+LINK_TTL+30:
            raise ValueError()
        return int(data['chat'])
    except (ValueError,KeyError,TypeError):
        raise SummaryAccessError('Ссылка устарела или недействительна. Вызовите /summary в чате.')


def authenticate(init_data, bot_token, now=None):
    now = int(time.time()) if now is None else now
    try:
        pairs=parse_qsl(init_data,strict_parsing=True)
        data=dict(pairs)
        if not bot_token or len(pairs)!=len(data) or len(init_data)>16384:
            raise ValueError()
        received=data.pop('hash')
        check='\n'.join(f'{k}={v}' for k,v in sorted(data.items()))
        secret=hmac.new(b'WebAppData',bot_token.encode(),hashlib.sha256).digest()
        expected=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(received,expected):
            raise ValueError()
        auth_date=int(data['auth_date'])
        if not now-INIT_TTL<=auth_date<=now+30:
            raise ValueError()
        user=json.loads(data['user'])
        if type(user['id']) is not int or user['id']<=0:
            raise ValueError()
        if not data.get('start_param'):
            raise SummaryAccessError('Эта ссылка не указывает чат. Вызовите /summary в нужном чате и откройте его кнопку.')
        chat=read_link(data.get('start_param',''),bot_token,now)
        return user['id'],chat
    except SummaryAccessError:
        raise
    except (ValueError,KeyError,TypeError):
        raise SummaryAccessError('Откройте саммари кнопкой после /summary в Telegram.')
