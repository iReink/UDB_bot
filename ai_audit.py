"""Thread-local snapshots of generation calls; never record headers or credentials."""
from datetime import datetime
import threading
import requests
import ai_http

_state = threading.local()


def begin():
    _state.calls = []


def take():
    calls = getattr(_state, 'calls', None) or []
    _state.calls = None
    return calls


def snapshot(value):
    """Keep prompts observable without duplicating image bytes in SQLite."""
    if isinstance(value,list):return [snapshot(item) for item in value]
    if isinstance(value,dict):
        if 'inlineData' in value:
            part=value['inlineData']
            return {'image':{'mimeType':part.get('mimeType'),'encoded_bytes':len(part.get('data',''))}}
        return {key:snapshot(item) for key,item in value.items()}
    return value


def post(url, *, provider, model, **kwargs):
    event = {'at': datetime.utcnow().isoformat(), 'provider': provider,
             'model': model, 'context': snapshot(kwargs.get('json')), 'status': 'error'}
    try:
        response = ai_http.post(url, **kwargs)
        event['http_status'] = response.status_code
        event['status'] = 'received' if response.ok else 'error'
        if response.ok:
            try:
                event['response'] = response.json()
            except ValueError:
                event['error'] = 'Invalid JSON response'
        else:
            event['error'] = 'HTTP ' + str(response.status_code)
        return response
    except requests.RequestException as exc:
        event['error'] = type(exc).__name__
        raise
    finally:
        calls = getattr(_state, 'calls', None)
        if calls is not None:
            calls.append(event)
