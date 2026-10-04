"""Read-only administrative AI dashboard. Queue execution remains in ai_tasks."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import time
from threading import RLock

from fastapi import HTTPException, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from web.profile_avatar import AvatarCache

TABLES = {'tasks': ('ai_tasks', 'task_type', 'result_text'),
          'type-checks': ('ai_type_checks', "'type_check'", 'result_type'),
          'search-plans': ('ai_search_plans', "'search_plan'", 'result_json')}


def decode(value):
    try:
        return json.loads(value) if value else None
    except (ValueError, TypeError):
        return value


def connection(path):
    conn = sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def answer_text(value):
    """Extract display text only; preserve the original API snapshot in detail."""
    data = decode(value)
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return value if isinstance(value, str) and not isinstance(data, list) else ''
    for key in ('response', 'answer', 'text'):
        if isinstance(data.get(key), str):
            return data[key]
    parts = []
    for candidate in data.get('candidates', [])[:1]:
        parts.extend(p['text'] for p in candidate.get('content', {}).get('parts', [])
                     if isinstance(p.get('text'), str) and not p.get('thought'))
    if parts:
        return '\n'.join(parts)
    choices = data.get('choices', [])
    if choices:
        content = choices[0].get('message', {}).get('content')
        return content if isinstance(content, str) else ''
    return ''


def union(brief=False):
    if brief:
        return ' UNION ALL '.join(f"SELECT '{queue}' queue,id,{kind} task_type,status,chat_id,user_id,created_at,finished_at FROM {table}" for queue,(table,kind,output) in TABLES.items())
    return ' UNION ALL '.join(
        f"SELECT '{queue}' queue,id,{kind} task_type,status,chat_id,user_id,created_at,updated_at,"
        f"prompt,{output} output,error_text,finished_at,{'response_message_id' if queue=='tasks' else 'NULL'} response_message_id FROM {table}"
        for queue, (table, kind, output) in TABLES.items())


def bounds(days, start, end):
    try:
        def parse(value):
            return datetime.fromisoformat(value.replace('Z', '+00:00')).replace(tzinfo=None) if len(value) == 10 else datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).replace(tzinfo=None)
        lower = parse(start) if start else datetime.utcnow() - timedelta(days=days)
        upper = parse(end) if end else datetime.utcnow() + timedelta(seconds=1)
        if end and len(end) == 10:
            upper += timedelta(days=1)
        if lower >= upper:
            raise ValueError()
        return lower.isoformat(), upper.isoformat()
    except (ValueError, OverflowError):
        raise HTTPException(422, 'Некорректный период')


_aggregate_cache={}
_aggregate_lock=RLock()


def list_tasks(path, *, days=7, start=None, end=None, chat=None, kind=None, model=None, page=1):
    lower, upper = bounds(days, start, end)
    # Latest snapshots select by rowid too, since several fallbacks may share a second.
    cte = f"""WITH tasks AS ({union()}), enriched AS (
        SELECT t.*, COALESCE(c.model,NULLIF(a.model,''),CASE WHEN t.created_at<(SELECT value FROM ai_audit_state WHERE key='enabled_at') AND t.status IN ('done','failed') AND a.token IS NULL THEN 'gemma4:e4b' END) actual_model,
               CASE WHEN c.model IS NULL AND NULLIF(a.model,'') IS NULL AND a.token IS NULL AND t.created_at<(SELECT value FROM ai_audit_state WHERE key='enabled_at') AND t.status IN ('done','failed') THEN 1 ELSE 0 END assumed_model,
               c.context_json,c.response_json,c.error call_error,a.outcome,r.response_json receipt
        FROM tasks t
        LEFT JOIN ai_model_calls c ON c.rowid=(SELECT rowid FROM ai_model_calls WHERE queue=t.queue AND task_id=t.id ORDER BY at DESC,rowid DESC LIMIT 1)
        LEFT JOIN ai_attempt_log a ON a.token=(SELECT token FROM ai_attempt_log WHERE queue=t.queue AND task_id=t.id ORDER BY at DESC,rowid DESC LIMIT 1)
        LEFT JOIN ai_receipts r ON r.token=a.token)
    """
    where = 'created_at>=? AND created_at<?'
    args = [lower, upper]
    for field, value in [('chat_id', chat), ('task_type', kind)]:
        if value is not None:
            where += f' AND {field}=?'; args.append(value)
    if model:
        where += ' AND (EXISTS(SELECT 1 FROM ai_model_calls c WHERE c.queue=enriched.queue AND c.task_id=enriched.id AND c.model=?) OR EXISTS(SELECT 1 FROM ai_attempt_log a WHERE a.queue=enriched.queue AND a.task_id=enriched.id AND a.model=?) OR (assumed_model=1 AND actual_model=?))'
        args.extend([model, model, model])
    with closing(connection(path)) as conn:
        total = conn.execute(cte + 'SELECT count(*) FROM enriched WHERE ' + where, args).fetchone()[0]
        page = min(page, max(1, (total + 49)//50))
        rows = [dict(r) for r in conn.execute(cte + '''SELECT queue,id,task_type,status,chat_id,user_id,created_at,actual_model,assumed_model,response_message_id,
            substr(COALESCE(context_json,prompt),1,220) context_preview,
            substr(CASE WHEN context_json IS NOT NULL THEN COALESCE(response_json,call_error,'') ELSE COALESCE(output,error_text,'') END,1,220) response_preview,
            output, response_json, outcome,receipt FROM enriched WHERE ''' + where + ' ORDER BY created_at DESC,id DESC,queue LIMIT 50 OFFSET ?', args + [(page-1)*50])]
        profile_ids = [r['id'] for r in rows if r['task_type'] == 'profile_update']
        profile_payloads = {r['id']: decode(r['payload_json']) for r in conn.execute(
            f"SELECT id,payload_json FROM ai_tasks WHERE id IN ({','.join('?' for _ in profile_ids)})", profile_ids)} if profile_ids else {}
        for row in rows:
            row['receipt'] = decode(row['receipt'])
            output = row.pop('output')
            response = row.pop('response_json')
            if row['task_type'] == 'type_check':
                row['classification_type'] = (output or '').strip()
                row['response_preview'] = row['classification_type']
            elif row['task_type'] == 'profile_update':
                profile = decode(output)
                if isinstance(profile, str):
                    profile = decode(profile.strip().removeprefix('```json').removesuffix('```').strip())
                payload = profile_payloads.get(row['id']) or {}
                row['response_preview'] = (profile.get('display_name') if isinstance(profile, dict) else None) or payload.get('display_name') or f"Пользователь {row['user_id']}"
            elif row['task_type'] == 'response':
                row['response_preview'] = answer_text(output or response)[:220]
        cache_key=(str(path.resolve()),path.stat().st_ino,days,start,end,chat,kind,model)
        with _aggregate_lock:
            cached=_aggregate_cache.get(cache_key)
            if cached and time.monotonic()-cached[0]<15:
                stats,chats,kinds,models=cached[1]
            else:
                # Audit calls include failures; historical logs contribute only when no snapshot exists.
                calls = f"""WITH tasks AS ({union(brief=True)}), calls AS (
                  SELECT c.queue,c.task_id,c.model,c.at,0 historical FROM ai_model_calls c
                  UNION ALL SELECT a.queue,a.task_id,a.model,a.at,1 FROM ai_attempt_log a
                  WHERE a.model<>'' AND NOT EXISTS(SELECT 1 FROM ai_model_calls c WHERE c.token=a.token)
                  UNION ALL SELECT t.queue,t.id,'gemma4:e4b',COALESCE(t.finished_at,t.created_at),1 FROM tasks t
                  WHERE t.status IN ('done','failed') AND t.created_at<(SELECT value FROM ai_audit_state WHERE key='enabled_at')
                  AND NOT EXISTS(SELECT 1 FROM ai_attempt_log a WHERE a.queue=t.queue AND a.task_id=t.id)
                  AND NOT EXISTS(SELECT 1 FROM ai_model_calls c WHERE c.queue=t.queue AND c.task_id=t.id))
                  SELECT c.model,t.task_type,count(*) count,sum(c.historical) historical FROM calls c
                  JOIN tasks t ON t.queue=c.queue AND t.id=c.task_id
                  WHERE c.at>=? AND c.at<?"""
                call_args = [lower, upper]
                for field, value in [('t.chat_id',chat),('t.task_type',kind),('c.model',model)]:
                    if value is not None:
                        calls += f' AND {field}=?';call_args.append(value)
                stats = [dict(r) for r in conn.execute(calls+' GROUP BY c.model,t.task_type',call_args)]
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                titles = {r[0]: r[1] for r in conn.execute('SELECT chat_id,title FROM web_chat_titles')} if 'web_chat_titles' in tables else {}
                chats = [{'id':r[0], 'title':titles.get(r[0]) or ('Личный чат' if r[0]>0 else 'Чат')} for r in conn.execute(f'WITH tasks AS ({union()}) SELECT DISTINCT chat_id FROM tasks ORDER BY chat_id')]
                kinds = [r[0] for r in conn.execute(f'WITH tasks AS ({union()}) SELECT DISTINCT task_type FROM tasks ORDER BY task_type')]
                models = [r[0] for r in conn.execute("SELECT model FROM ai_model_calls WHERE model<>'' UNION SELECT model FROM ai_attempt_log WHERE model<>'' UNION SELECT 'gemma4:e4b' ORDER BY model")]
                _aggregate_cache[cache_key]=(time.monotonic(),(stats,chats,kinds,models))
                if len(_aggregate_cache)>64:_aggregate_cache.pop(next(iter(_aggregate_cache)))
    return dict(rows=rows,total=total,page=page,pages=max(1,(total+49)//50),stats=stats,chats=chats,kinds=kinds,models=models,updated_at=datetime.utcnow().isoformat())


def detail(path, queue, ident):
    if queue not in TABLES:
        raise HTTPException(404)
    with closing(connection(path)) as conn:
        row = conn.execute(f'SELECT * FROM {TABLES[queue][0]} WHERE id=?',(ident,)).fetchone()
        if row is None:
            raise HTTPException(404)
        task = dict(row)
        calls = [dict(r) for r in conn.execute('SELECT * FROM ai_model_calls WHERE queue=? AND task_id=? ORDER BY at,sequence',(queue,ident))]
        for call in calls:
            call['context'] = decode(call.pop('context_json'))
            call['response'] = decode(call.pop('response_json'))
        attempts = [dict(r) for r in conn.execute('SELECT a.*,r.response_json receipt FROM ai_attempt_log a LEFT JOIN ai_receipts r ON r.token=a.token WHERE a.queue=? AND a.task_id=? ORDER BY a.at,a.rowid',(queue,ident))]
        for attempt in attempts:
            attempt['receipt'] = decode(attempt['receipt'])
        rag = None
        if queue == 'tasks' and conn.execute("SELECT 1 FROM sqlite_master WHERE name='ai_rag_queries'").fetchone():
            row = conn.execute('SELECT * FROM ai_rag_queries WHERE task_id=?', (ident,)).fetchone()
            if row:
                rag = dict(row)
                rag.pop('token', None)
                rag.pop('lease_until', None)
                rag['result'] = decode(rag.pop('result_json'))
        if rag is None and queue=='tasks':
            payload=decode(task.get('payload_json')) or {}
            if payload.get('mechanics'):
                rag=dict(state='inherited',query=payload['mechanics'].get('query'),
                    result=dict(payload['mechanics'],sql_task_id=payload.get('mechanics_sql_task_id')),
                    sql_task_id=payload.get('mechanics_sql_task_id'))
        return dict(task=task,calls=calls,attempts=attempts,rag=rag)


def history(path, queue, ident):
    task = detail(path,queue,ident)['task']
    kind = task.get('task_type')
    if queue != 'tasks' or kind not in ('profile_update','chat_summary'):
        return {'items': []}
    with closing(connection(path)) as conn:
        if kind == 'profile_update':
            table, order, scope = 'ai_profiles','profile_date','chat_id=? AND user_id=?'
            args = [task['chat_id'],task['user_id']]
        else:
            table, order, scope = 'ai_summary','window_end','chat_id=?'
            args = [task['chat_id']]
        current = conn.execute(f"SELECT * FROM {table} WHERE {scope} AND task_id=? AND status='done'",args+[ident]).fetchone()
        if not current:
            return {'items': []}
        previous = list(conn.execute(f"SELECT * FROM {table} WHERE {scope} AND status='done' AND {order}<? ORDER BY {order} DESC LIMIT 2",args+[current[order]]))
        following = list(conn.execute(f"SELECT * FROM {table} WHERE {scope} AND status='done' AND {order}>? ORDER BY {order} LIMIT 2",args+[current[order]]))
        items = [dict(r) for r in reversed(previous)]+[dict(current)]+[dict(r) for r in following]
        prior = previous[0] if previous else None
        return dict(items=items,index=len(previous),previous_profile=decode(prior['profile_json']) if prior and kind=='profile_update' else None)


def register(app, templates, db_path, require_session, admin_ids, bot_token=''):
    avatars=AvatarCache(db_path.parent/'profile_avatar_cache',bot_token)
    def authorize(request):
        payload = require_session(request)
        if int(payload['telegram_user_id']) not in admin_ids:
            raise HTTPException(403, 'Доступ только для администратора')

    def response(data):
        return JSONResponse(data,headers={'Cache-Control':'no-store'})

    @app.get('/ai_tasks',response_class=HTMLResponse)
    def page(request: Request):
        return templates.TemplateResponse('ai_tasks.html',{'request':request},headers={'Cache-Control':'no-store'})

    @app.get('/api/ai-dashboard/tasks')
    def tasks(request: Request, days: int=Query(7,ge=1,le=3660), start: str|None=None, end: str|None=None,
              chat: int|None=None, kind: str|None=None, model: str|None=None, page: int=Query(1,ge=1)):
        authorize(request)
        return response(list_tasks(db_path,days=days,start=start,end=end,chat=chat,kind=kind,model=model,page=page))

    @app.get('/api/ai-dashboard/detail/{queue}/{ident}')
    def task_detail(request: Request, queue: str, ident: int):
        authorize(request)
        return response(detail(db_path,queue,ident))

    @app.get('/api/ai-dashboard/history/{queue}/{ident}')
    def task_history(request: Request, queue: str, ident: int):
        authorize(request)
        return response(history(db_path,queue,ident))

    @app.get('/api/ai-dashboard/avatar/{queue}/{ident}')
    def avatar(request: Request, queue: str, ident: int):
        authorize(request)
        task=detail(db_path,queue,ident)['task']
        if queue!='tasks' or task.get('task_type')!='profile_update':raise HTTPException(404)
        photo=avatars.get(task['user_id'])
        if photo is None:raise HTTPException(404)
        return FileResponse(photo,media_type='image/jpeg',headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
