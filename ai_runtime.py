"""Shared routing, fenced leases and model budgets. No provider secrets in SQLite."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
import threading
from schema_once import once as schema_once, forget as forget_schema
from contextlib import closing
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MODES = {
    "api": "Только внешний API",
    "local": "Только локальная модель",
    "local_api": "Локальная, если недоступна — API",
    "api_local": "API → локальная",
    "off": "ИИ-ответы отключены",
}
TABLES = {"tasks": "ai_tasks", "type-checks": "ai_type_checks", "search-plans": "ai_search_plans"}
LEASE_SECONDS = 60
MODEL_LIGHT = "openai/gpt-oss-20b"
MODEL_HEAVY = "openai/gpt-oss-120b"
MODEL_GOOGLE_PRIMARY = "gemini-3.5-flash-lite"
MODEL_GOOGLE_SECONDARY = "gemini-3.1-flash-lite"
MODEL_GOOGLE_LAST = "gemma-4-31b-it"
delivery_context = threading.local()


class DeliveryError(Exception):
    """Delivery outcome is unknown; it must not trigger LLM regeneration."""


def fail_delivery(queue, task_id):
    with closing(connect()) as conn:
        conn.execute(f"UPDATE {TABLES[queue]} SET status='failed',error_text='Telegram delivery unknown; no automatic resend',finished_at=?,lease_until=NULL WHERE id=? AND status='processing'", (stamp(), task_id))
        if queue == "tasks":
            for table, column in (("ai_profiles", "task_id"), ("ai_summary", "task_id"), ("ai_data_analyses", "response_task_id")):
                conn.execute(f"UPDATE {table} SET status='failed',error_text='Telegram delivery unknown',updated_at=? WHERE {column}=? AND status IN ('pending','processing')", (stamp(), task_id))
        conn.commit()


def connect():
    from ai_tasks import get_connection
    return get_connection()


def now():
    return datetime.utcnow().replace(microsecond=0)


def stamp(seconds=0):
    return (now() + timedelta(seconds=seconds)).isoformat()


def ensure_schema(conn):
    from ai_grounding import ensure_schema as ensure_grounding
    ensure_grounding(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS settings (chat_id INTEGER, name TEXT, value, UNIQUE(chat_id,name))")
    conn.execute("CREATE TABLE IF NOT EXISTS ai_workers (worker_id TEXT PRIMARY KEY, provider TEXT NOT NULL, queues_json TEXT NOT NULL, heartbeat TEXT NOT NULL, unavailable_until TEXT)")
    if 'task_types_json' not in {r[1] for r in conn.execute('PRAGMA table_info(ai_workers)')}:
        conn.execute("ALTER TABLE ai_workers ADD COLUMN task_types_json TEXT NOT NULL DEFAULT '[]'")
    conn.execute("CREATE TABLE IF NOT EXISTS ai_provider_state (provider TEXT PRIMARY KEY, unavailable_until TEXT NOT NULL, reason TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS ai_attempt_log (token TEXT PRIMARY KEY, queue TEXT NOT NULL, task_id INTEGER NOT NULL, provider TEXT, model TEXT, usage_json TEXT NOT NULL, outcome TEXT NOT NULL, at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS ai_audit_state (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    conn.execute("INSERT OR IGNORE INTO ai_audit_state VALUES ('enabled_at',?)", (stamp(),))
    conn.execute("CREATE TABLE IF NOT EXISTS ai_model_calls (token TEXT NOT NULL, sequence INTEGER NOT NULL, queue TEXT NOT NULL, task_id INTEGER NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, at TEXT NOT NULL, context_json TEXT NOT NULL, response_json TEXT, status TEXT NOT NULL, error TEXT, PRIMARY KEY(token,sequence))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_calls_task ON ai_model_calls(queue,task_id,at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_calls_at_model ON ai_model_calls(at,model)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_attempt_task ON ai_attempt_log(queue,task_id,at)")
    conn.execute("CREATE TABLE IF NOT EXISTS ai_receipts (token TEXT PRIMARY KEY, worker_id TEXT NOT NULL, queue TEXT NOT NULL, task_id INTEGER NOT NULL, accepted_at TEXT NOT NULL, response_json TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS ai_model_usage (id INTEGER PRIMARY KEY, model TEXT NOT NULL, at TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, cached_tokens INTEGER NOT NULL DEFAULT 0)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_usage_model_at ON ai_model_usage(model,at)")
    for table in TABLES.values():
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            continue
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_dashboard_created ON {table}(created_at)")
        columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, definition in {
            "worker_id": "TEXT", "lease_token": "TEXT", "provider": "TEXT",
            "next_provider": "TEXT", "retry_at": "TEXT", "transport_attempt": "INTEGER NOT NULL DEFAULT 0", "minute_retry": "INTEGER NOT NULL DEFAULT 0", "refusal_kind": "TEXT",
        }.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_ready ON {table}(status,retry_at,created_at)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_lease ON {table}(status,lease_until)")


@schema_once(lambda: __import__('ai_tasks').DB_FILE)
def _initialize_once():
    from ai_tasks import ensure_ai_tables
    ensure_ai_tables()
    with closing(connect()) as conn:
        ensure_schema(conn)
        conn.commit()


def initialize(force=True):
    if force:
        from ai_tasks import DB_FILE
        forget_schema(DB_FILE)
    _initialize_once()


def mode(chat_id, conn=None):
    if conn is None:
        with closing(connect()) as db:
            return mode(chat_id, db)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone():
        return "local"
    row = conn.execute("SELECT value FROM settings WHERE chat_id=? AND name='ai_source'", (chat_id,)).fetchone()
    return str(row[0]) if row and str(row[0]) in MODES else "local"


def enabled(chat_id):
    return mode(chat_id) != "off"


def providers(value):
    return {"api": ("groq",), "local": ("local",), "local_api": ("local", "groq"), "api_local": ("groq", "local"), "off": ()}[value]


def set_mode(chat_id, value):
    if value not in MODES:
        raise ValueError("Unknown AI source")
    initialize()
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO settings(chat_id,name,value) VALUES (?,'ai_source',?) ON CONFLICT(chat_id,name) DO UPDATE SET value=excluded.value", (chat_id, value))
        for table in TABLES.values():
            if value == "off":
                conn.execute(f"UPDATE {table} SET status='cancelled', lease_token=NULL, lease_until=NULL, updated_at=?, finished_at=? WHERE chat_id=? AND status IN ('pending','processing')", (stamp(), stamp(), chat_id))
            else:
                allowed = providers(value)
                # Already accepted results are finished by their single owner.
                conn.execute(f"UPDATE {table} SET status='pending', lease_token=NULL, lease_until=NULL, worker_id=NULL, next_provider=NULL, retry_at=NULL WHERE chat_id=? AND status='processing' AND (provider IS NULL OR provider NOT IN ({','.join('?' for _ in allowed)})) AND NOT EXISTS (SELECT 1 FROM ai_receipts r WHERE r.token={table}.lease_token)", (chat_id, *allowed))
                conn.execute(f"UPDATE {table} SET next_provider=NULL,retry_at=NULL WHERE chat_id=? AND status='pending'", (chat_id,))
        if value == "off":
            for table in ("ai_profiles", "ai_summary", "ai_data_analyses"):
                conn.execute(f"UPDATE {table} SET status='cancelled',updated_at=? WHERE chat_id=? AND status IN ('pending','processing')", (stamp(), chat_id))
        conn.commit()


def heartbeat(worker_id, provider, queues, ready=True, task_types=None):
    if provider not in ("local", "groq") or not worker_id or not queues or any(q not in TABLES for q in queues):
        raise ValueError("Invalid worker registration")
    task_types=task_types or []
    if task_types and (provider!='groq' or set(task_types)!={'photo_story','photo_story_merge'}):
        raise ValueError('Invalid photo worker capabilities')
    initialize(force=False)
    with closing(connect()) as conn:
        conn.execute("INSERT INTO ai_workers(worker_id,provider,queues_json,heartbeat,unavailable_until,task_types_json) VALUES (?,?,?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET provider=excluded.provider,queues_json=excluded.queues_json,heartbeat=excluded.heartbeat,unavailable_until=excluded.unavailable_until,task_types_json=excluded.task_types_json", (worker_id, provider, json.dumps(queues), stamp(), None if ready else stamp(30),json.dumps(task_types)))
        conn.commit()


def available(conn, provider, queue, task_type=None):
    blocked = conn.execute("SELECT unavailable_until FROM ai_provider_state WHERE provider=?", (provider,)).fetchone()
    if blocked and blocked[0] > stamp():
        return False
    for worker in conn.execute("SELECT * FROM ai_workers WHERE provider=? AND heartbeat>=? AND (unavailable_until IS NULL OR unavailable_until<=?)", (provider, stamp(-30), stamp())):
        if queue=='tasks' and (task_type in ('photo_story','photo_story_merge'))!=bool(json.loads(worker['task_types_json'])):
            continue
        if queue in json.loads(worker["queues_json"]):
            return True
    return False


def ready_queues():
    result=[]
    with closing(connect()) as conn:
        for queue,table in TABLES.items():
            if conn.execute(f"SELECT 1 FROM {table} WHERE status='pending' AND (retry_at IS NULL OR retry_at<=?) LIMIT 1",(stamp(),)).fetchone() or conn.execute(f"SELECT 1 FROM {table} WHERE status='processing' AND (lease_until IS NULL OR lease_until<=?) LIMIT 1",(stamp(),)).fetchone():result.append(queue)
        if conn.execute("SELECT 1 FROM ai_tasks WHERE rag_state='queued' AND status='pending' LIMIT 1").fetchone():result.append('rag')
    return result


def claim(queue, worker_id):
    table = TABLES[queue]
    with closing(connect()) as conn:
        if not conn.execute(f"SELECT 1 FROM {table} WHERE status='pending' AND (retry_at IS NULL OR retry_at<=?) LIMIT 1",(stamp(),)).fetchone() and not conn.execute(f"SELECT 1 FROM {table} WHERE status='processing' AND (lease_until IS NULL OR lease_until<=?) LIMIT 1",(stamp(),)).fetchone():
            return None
        conn.execute("BEGIN IMMEDIATE")
        worker = conn.execute("SELECT * FROM ai_workers WHERE worker_id=? AND heartbeat>=?", (worker_id, stamp(-30))).fetchone()
        if not worker or queue not in json.loads(worker["queues_json"]):
            raise ValueError("Worker must register and heartbeat")
        if queue == 'tasks':
            from rag_search import expire as expire_rag
            expire_rag(conn)
        order = "priority DESC,created_at" if queue == "tasks" else "created_at"
        route_modes=("local","local_api","api_local") if worker["provider"]=="local" else ("api","local_api","api_local")
        if queue=="tasks" and json.loads(worker["task_types_json"]):route_modes=("local","api","local_api","api_local")
        capability=""
        if queue=="tasks":
            capability=" AND task_type IN ('photo_story','photo_story_merge')" if json.loads(worker["task_types_json"]) else " AND task_type NOT IN ('photo_story','photo_story_merge','imagegen') AND coalesce(rag_state,'')<>'queued'"
        rows = conn.execute(f"SELECT * FROM {table} WHERE ((status='pending' AND (retry_at IS NULL OR retry_at<=?)) OR (status='processing' AND (lease_until IS NULL OR lease_until<=?))) AND coalesce((SELECT value FROM settings WHERE settings.chat_id={table}.chat_id AND name='ai_source'),'local') IN ({','.join('?' for _ in route_modes)}){capability} ORDER BY {order} LIMIT 100", (stamp(), stamp(),*route_modes)).fetchall()
        availability={};modes={}
        def can(provider,kind):
            key=(provider,kind)
            if key not in availability:availability[key]=available(conn,provider,queue,kind)
            return availability[key]
        for row in rows:
            if row['chat_id'] not in modes:modes[row['chat_id']]=mode(row['chat_id'],conn)
            routing_mode=modes[row['chat_id']]

            if queue=='tasks' and row['task_type'] in ('profile_update','photo_story','photo_story_merge'):
                payload=json.loads(row['payload_json'] or '{}')
                nightly=row['task_type']=='profile_update' and payload.get('background',True) or bool(payload.get('daily_id')) and not payload.get('notify_chat')
                if nightly:
                    local=now().replace(tzinfo=timezone.utc).astimezone(ZoneInfo('Asia/Yekaterinburg'))
                    if not 4<=local.hour<7:continue
                    if conn.execute("SELECT value FROM ai_rag_state WHERE key='night_open'").fetchone() and conn.execute("SELECT value FROM ai_rag_state WHERE key='night_open'").fetchone()[0]=='1':continue
                    if row['task_type']!='profile_update' and conn.execute("SELECT 1 FROM ai_tasks WHERE task_type='profile_update' AND status IN ('pending','processing') LIMIT 1").fetchone():continue
            if queue=='tasks' and row['task_type']=='imagegen':
                continue  # Owned by the independent VPS image loop, never by PC workers.
            if queue=='tasks':
                photo_task=row['task_type'] in ('photo_story','photo_story_merge')
                photo_worker=bool(json.loads(worker['task_types_json']))
                if photo_task!=photo_worker:
                    continue
            if queue == 'tasks' and row['rag_state'] == 'queued':
                continue
            receipt = conn.execute("SELECT accepted_at,response_json,queue,task_id FROM ai_receipts WHERE token=?", (row["lease_token"],)).fetchone()
            retry_completed = False
            if receipt and row['status']=='pending' and receipt['queue']==queue and receipt['task_id']==row['id']:
                try:
                    result=json.loads(receipt['response_json'] or '{}')
                    retry_completed=isinstance(result,dict) and result.get('ok') is True and result.get('status') in ('retry','waiting') and result.get('task_id')==row['id']
                except (ValueError,TypeError):
                    pass
            if receipt and not retry_completed:
                # Only a completed retry receipt permits a new generation. Keep
                # the old receipt to fence duplicate HTTP results from that lease.
                if receipt[0] < stamp(-900):
                    conn.execute(f"UPDATE {table} SET status='failed',error_text='Result delivery interrupted; manual reconciliation required',finished_at=? WHERE id=?", (stamp(), row["id"]))
                continue
            allowed = providers(routing_mode)
            if queue=='tasks' and row['task_type'] in ('photo_story','photo_story_merge'):
                allowed=('groq',) if routing_mode!='off' else ()
            if queue=='tasks' and row['task_type'] in ('web_grounding','maps_grounding') and 'groq' in allowed:
                allowed=('groq',)+tuple(p for p in allowed if p!='groq')
            if not allowed:
                conn.execute(f"UPDATE {table} SET status='cancelled',lease_token=NULL,finished_at=? WHERE id=?", (stamp(), row["id"]))
                continue
            preferred = row["next_provider"] if row["next_provider"] in allowed else allowed[0]
            sequence = (preferred,) + tuple(p for p in allowed if p != preferred)
            chosen = next((p for p in sequence if can(p,row['task_type'] if queue=='tasks' else None)), None)
            if chosen != worker["provider"]:
                continue
            token = uuid.uuid4().hex
            conn.execute(f"UPDATE {table} SET status='processing',worker_id=?,provider=?,lease_token=?,lease_until=?,updated_at=?,next_provider=NULL WHERE id=?", (worker_id, chosen, token, stamp(LEASE_SECONDS), stamp(), row["id"]))
            task = dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (row["id"],)).fetchone())
            task["task_type"] = task.get("task_type") or {"type-checks": "type_check", "search-plans": "search_plan"}.get(queue)
            task["payload"] = json.loads(task.get("payload_json") or "{}")
            task["queue"] = queue
            from ai_tasks import apply_creator_policy, creator_system_instruction
            requester = task["user_id"] if task['task_type'] in ('response', 'mechanics', 'photo_story','photo_story_merge', 'data_analysis_response','web_grounding','maps_grounding','maps_translation') else None
            if task['task_type'] in ('photo_story','photo_story_merge'):
                from ai_tasks import CREATOR_POLICY_MARKER,CREATOR_REPLY_GUARD
                if task['prompt'].startswith(CREATOR_POLICY_MARKER):task['prompt']=task['prompt'].split('\n\n',1)[-1]
                task['prompt']=task['prompt'].removesuffix('\n\n'+CREATOR_REPLY_GUARD)
            elif task['task_type'] not in ('maps_grounding','grounding_notice'):
                task["prompt"] = apply_creator_policy(task["prompt"], requester_user_id=requester)
            task['system_instruction'] = '' if task['task_type'] in ('photo_story','photo_story_merge') else creator_system_instruction(task['user_id'], 'response' if task['task_type']=='mechanics' else task['task_type'])
            conn.commit()
            return task
        conn.commit()
    return None


def owns(conn, queue, task_id, worker_id, token):
    row = conn.execute(f"SELECT * FROM {TABLES[queue]} WHERE id=?", (task_id,)).fetchone()
    return row if row and row["status"] == "processing" and row["worker_id"] == worker_id and row["lease_token"] == token and row["lease_until"] and row["lease_until"] > stamp() and mode(row["chat_id"], conn) != "off" else None


def renew(queue, task_id, worker_id, token):
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not owns(conn, queue, task_id, worker_id, token):
            return False
        conn.execute(f"UPDATE {TABLES[queue]} SET lease_until=? WHERE id=?", (stamp(LEASE_SECONDS), task_id))
        conn.commit()
        return True


def defer(queue, task_id, worker_id, token, reason, retry_seconds=None, provider_cooldown=True, refusal_kind="transport"):
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = owns(conn, queue, task_id, worker_id, token)
        if not row:
            raise ValueError("Stale lease")
        allowed = providers(mode(row["chat_id"], conn))
        if queue=='tasks' and row['task_type'] in ('photo_story','photo_story_merge'):
            allowed=('groq',) if mode(row['chat_id'],conn)!='off' else ()
        payload=json.loads(row['payload_json'] or '{}')
        background=queue=='tasks' and (row['task_type']=='chat_summary' and payload.get('background',True) or row['task_type']=='profile_update' and payload.get('background',True) or row['task_type'] in ('photo_story','photo_story_merge') and payload.get('daily_id') and not payload.get('notify_chat'))
        exhausted=refusal_kind=='permanent' or not background and (refusal_kind=='daily' or refusal_kind=='minute' and row['minute_retry']>=1 or refusal_kind=='transport' and row['transport_attempt']>=2)
        alternative = next((p for p in allowed if p != row["provider"]), None)
        if exhausted and not (refusal_kind=='daily' and alternative and row['transport_attempt']==0):return False
        wait = max(1, int(retry_seconds)) if retry_seconds is not None else min(300, 30 * 2 ** min(4, row["transport_attempt"]))
        if refusal_kind=="minute" and not background:wait=min(60,wait)
        if refusal_kind=="transport" and not background:wait=(10,30)[min(row["transport_attempt"],1)]
        if alternative:
            wait = 0 if available(conn, alternative, queue,row['task_type'] if queue=='tasks' else None) else min(wait, 30)
        if provider_cooldown:
            conn.execute("INSERT INTO ai_provider_state VALUES (?,?,?) ON CONFLICT(provider) DO UPDATE SET unavailable_until=excluded.unavailable_until,reason=excluded.reason", (row["provider"], stamp(30), reason[:200]))
        conn.execute(f"UPDATE {TABLES[queue]} SET status='pending',lease_token=NULL,lease_until=NULL,worker_id=NULL,next_provider=?,retry_at=?,transport_attempt=transport_attempt+?,minute_retry=minute_retry+?,refusal_kind=?,error_text=?,updated_at=? WHERE id=?", (alternative, stamp(wait),int(refusal_kind!="minute"),int(refusal_kind=="minute"),refusal_kind,reason[:300], stamp(), task_id))
        conn.commit()
        return True


def accept_result(queue, task_id, worker_id, token):
    """Atomically accept once before any downstream task or Telegram side effect."""
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        receipt = conn.execute("SELECT * FROM ai_receipts WHERE token=?", (token,)).fetchone()
        if receipt:
            if (receipt["worker_id"], receipt["queue"], receipt["task_id"]) != (worker_id, queue, task_id):
                raise ValueError("Invalid receipt")
            return json.loads(receipt["response_json"]) if receipt["response_json"] else {"ok": True, "status": "accepted"}
        if not owns(conn, queue, task_id, worker_id, token):
            raise ValueError("Stale lease")
        conn.execute("INSERT INTO ai_receipts VALUES (?,?,?,?,?,NULL)", (token, worker_id, queue, task_id, stamp()))
        conn.execute(f"UPDATE {TABLES[queue]} SET lease_until=? WHERE id=?", (stamp(900), task_id))
        conn.commit()
        return None


def finish_receipt(token, result):
    with closing(connect()) as conn:
        conn.execute("UPDATE ai_receipts SET response_json=? WHERE token=?", (json.dumps(result), token))
        conn.commit()


def record_attempt(queue, task_id, token, metadata, outcome):
    with closing(connect()) as conn:
        row = conn.execute(f"SELECT provider FROM {TABLES[queue]} WHERE id=?", (task_id,)).fetchone()
        conn.execute("INSERT INTO ai_attempt_log VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(token) DO UPDATE SET outcome=excluded.outcome", (token, queue, task_id, str(metadata.get('provider') or (row[0] if row else '')), str(metadata.get('model', '')), json.dumps(metadata.get('usage') or {}), outcome[:100], stamp()))
        for sequence, call in enumerate(metadata.get('calls') or []):
            if not isinstance(call, dict):
                continue
            conn.execute("INSERT OR IGNORE INTO ai_model_calls VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (token, sequence, queue, task_id, str(call.get('provider', '')), str(call.get('model', '')),
                          str(call.get('at') or stamp()), json.dumps(call.get('context'), ensure_ascii=False),
                          json.dumps(call['response'], ensure_ascii=False) if 'response' in call else None, str(call.get('status', 'error')),
                          str(call.get('error') or '')))
        conn.commit()


def model_limits(model):
    from photo_story import VISION_MODELS
    if model in VISION_MODELS:
        defaults={'RPM':5,'RPD':20,'TPM':250000,'TPD':0}; prefix='AI_VISION_'
    elif model=='gemini-robotics-er-2-preview':
        defaults={'RPM':5,'RPD':20,'TPM':250000,'TPD':0}; prefix='ROBOTICS_'
    elif model in ('gemini-2.5-flash','gemini-2.5-flash-lite'):
        defaults={'RPM':5 if model.endswith('flash') else 10,'RPD':20,'TPM':250000,'TPD':0}; prefix='GEMINI_25_'
    elif model.startswith('openai/'):
        defaults={'RPM':30,'RPD':1000,'TPM':8000,'TPD':200000}; prefix='GROQ_'
    elif model.startswith('gemma-'):
        defaults={'RPM':30,'RPD':14400,'TPM':16000,'TPD':0}; prefix='GEMMA_'
    else:
        defaults={'RPM':15,'RPD':500,'TPM':250000,'TPD':0}; prefix='GEMINI_'
    return {name:int(os.getenv(prefix+name,str(value))) for name,value in defaults.items()}


def quota_day_start(model):
    if model.startswith('openai/'):
        return stamp(-86400)
    local=now().replace(tzinfo=timezone.utc).astimezone(ZoneInfo('America/Los_Angeles'))
    return local.replace(hour=0,minute=0,second=0).astimezone(timezone.utc).replace(tzinfo=None).isoformat()


def quota_day_wait(model):
    if model.startswith('openai/'):
        return 86400
    local=now().replace(tzinfo=timezone.utc).astimezone(ZoneInfo('America/Los_Angeles'))
    end=(local+timedelta(days=1)).replace(hour=0,minute=0,second=0).astimezone(timezone.utc).replace(tzinfo=None)
    return max(1,int((end-now()).total_seconds())+1)


def budget_kind(model,input_tokens=1,output_tokens=0):
    with closing(connect()) as conn:
        limits=model_limits(model)
        count=input_tokens if not model.startswith('openai/') else input_tokens+output_tokens
        if count>limits['TPM']:return 'permanent'
        row=conn.execute('SELECT count(*),coalesce(sum(input_tokens+output_tokens-cached_tokens),0) FROM ai_model_usage WHERE model=? AND at>=?',(model,quota_day_start(model))).fetchone()
        return 'daily' if row[0]>=limits['RPD'] or limits['TPD'] and row[1]+count>limits['TPD'] else 'minute'


def budget_wait(model, input_tokens=1, output_tokens=0, conn=None):
    if conn is None:
        with closing(connect()) as db:
            return budget_wait(model,input_tokens,output_tokens,db)
    limits=model_limits(model)
    google=not model.startswith('openai/')
    count=input_tokens if google else input_tokens+output_tokens
    if any(limits[k]<=0 for k in ('RPM','RPD','TPM')):
        return quota_day_wait(model)
    if count>limits['TPM']:
        return quota_day_wait(model)  # caller must shrink or use another model
    day_start=quota_day_start(model)
    token_sql='input_tokens' if google else 'max(0,input_tokens+output_tokens-cached_tokens)'
    daily=conn.execute(f'SELECT count(*),coalesce(sum({token_sql}),0),min(at) FROM ai_model_usage WHERE model=? AND at>=?',(model,day_start)).fetchone()
    if daily[0]>=limits['RPD'] or limits['TPD'] and daily[1]+count>limits['TPD']:
        return quota_day_wait(model) if google else max(1,int((datetime.fromisoformat(daily[2])+timedelta(days=1)-now()).total_seconds())+1)
    recent=conn.execute(f'SELECT count(*),coalesce(sum({token_sql}),0) FROM ai_model_usage WHERE model=? AND at>?',(model,stamp(-60))).fetchone()
    if recent[0]<limits['RPM'] and recent[1]+count<=limits['TPM']:return 0
    recent=conn.execute('SELECT at,input_tokens,output_tokens,cached_tokens FROM ai_model_usage WHERE model=? AND at>? ORDER BY at',(model,stamp(-60))).fetchall()
    tokens=lambda r:max(0,r['input_tokens']+(0 if google else r['output_tokens'])-(0 if google else r['cached_tokens']))
    minute=[r for r in recent if r['at']>stamp(-60)]
    while len(minute)>=limits['RPM'] or sum(tokens(r) for r in minute)+count>limits['TPM']:
        removed=minute.pop(0)
        delay=max(1,int((datetime.fromisoformat(removed['at'])+timedelta(seconds=60)-now()).total_seconds())+1)
        if len(minute)<limits['RPM'] and sum(tokens(r) for r in minute)+count<=limits['TPM']:
            return delay
    return 0


def reserve(model, input_tokens, output_tokens, tool=None):
    """Atomic shared reservation; Google's minute budget counts input tokens."""
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if budget_wait(model,input_tokens,output_tokens,conn):
            return None
        if tool:
            from ai_grounding import tool_wait, family
            if tool_wait(tool,model,conn):return None
        cursor = conn.execute("INSERT INTO ai_model_usage(model,at,input_tokens,output_tokens) VALUES (?,?,?,?)", (model, stamp(), input_tokens, output_tokens))
        if tool:conn.execute('INSERT INTO ai_tool_usage(reservation,tool,family,at) VALUES (?,?,?,?)',(cursor.lastrowid,tool,family(model),stamp()))
        conn.commit()
        return cursor.lastrowid


def settle(reservation, usage):
    with closing(connect()) as conn:
        conn.execute("UPDATE ai_model_usage SET input_tokens=?,output_tokens=?,cached_tokens=? WHERE id=?", (int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)), int((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)), reservation))
        conn.commit()


def cool_model(model, seconds, reason="minute"):
    # A reservation-sized blocker uses the same durable budget path.
    with closing(connect()) as conn:
        conn.execute("INSERT INTO ai_provider_state VALUES (?,?,?) ON CONFLICT(provider) DO UPDATE SET unavailable_until=excluded.unavailable_until,reason=excluded.reason", (model, stamp(max(1, seconds)), reason))
        conn.commit()


def model_ready(model):
    with closing(connect()) as conn:
        row = conn.execute("SELECT unavailable_until FROM ai_provider_state WHERE provider=?", (model,)).fetchone()
        return not row or row[0] <= stamp()
