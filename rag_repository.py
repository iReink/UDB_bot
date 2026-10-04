"""Durable RAG manifests. Source messages remain authoritative in SQLite."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

VERSION = 'embedding2-768-v1'
MODEL = 'gemini-embedding-2'
COLLECTION = 'udb_messages_embedding2_768_v1'
ZONE = ZoneInfo('Asia/Yekaterinburg')
BOOTSTRAP_BATCH = 500


def connect(path=None):
    if path is None:
        from ai_tasks import get_connection
        conn = get_connection()
    else:
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=5000')
    return conn


def stamp(seconds=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(tzinfo=None).isoformat(timespec='microseconds')


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def message_hash(row):
    return digest([row['chat_id'], row['message_id'], row['user_id'], row['date'], row['message_text']])


def aware(value):
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return dt.replace(tzinfo=ZONE) if dt.tzinfo is None else dt.astimezone(ZONE)


def tokens(text):
    from ai_providers import tokenizer
    return len(tokenizer().encode(text, disallowed_special=()))


def exists(conn, name):
    return conn.execute('SELECT 1 FROM sqlite_master WHERE name=?', (name,)).fetchone() is not None


def ensure_schema(conn):
    statements = [
        'CREATE TABLE IF NOT EXISTS ai_rag_state(key TEXT PRIMARY KEY,value TEXT NOT NULL)',
        '''CREATE TABLE IF NOT EXISTS ai_rag_runs(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,state TEXT NOT NULL,
           started_at TEXT NOT NULL,updated_at TEXT NOT NULL,stats_json TEXT NOT NULL DEFAULT '{}',finished_at TEXT)''',
        '''CREATE TABLE IF NOT EXISTS ai_rag_message_status(chat_id INTEGER,message_id INTEGER,day TEXT,
           source_hash TEXT,eligible INTEGER NOT NULL,reason TEXT NOT NULL,initial INTEGER NOT NULL,
           indexed INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(chat_id,message_id))''',
        'CREATE INDEX IF NOT EXISTS idx_rag_messages_day ON ai_rag_message_status(chat_id,day,eligible)',
        'CREATE INDEX IF NOT EXISTS idx_rag_messages_pending ON ai_rag_message_status(initial,eligible,indexed)',
        '''CREATE TABLE IF NOT EXISTS ai_rag_days(chat_id INTEGER,day TEXT,revision INTEGER NOT NULL DEFAULT 1,
           generation TEXT,active_generation TEXT,status TEXT NOT NULL DEFAULT 'pending',retry_at TEXT,
           updated_at TEXT NOT NULL,PRIMARY KEY(chat_id,day))''',
        '''CREATE TABLE IF NOT EXISTS ai_rag_chunks(id TEXT PRIMARY KEY,chat_id INTEGER,day TEXT,generation TEXT,
           min_id INTEGER,max_id INTEGER,start_at TEXT,end_at TEXT,text TEXT,content_hash TEXT,
           parts_json TEXT,state TEXT NOT NULL DEFAULT 'prepared',vector_json TEXT)''',
        'CREATE INDEX IF NOT EXISTS idx_rag_chunks_day ON ai_rag_chunks(chat_id,day,generation,state)',
        '''CREATE TABLE IF NOT EXISTS ai_rag_chunk_messages(chunk_id TEXT,chat_id INTEGER,message_id INTEGER,
           part INTEGER,source_hash TEXT,PRIMARY KEY(chunk_id,message_id,part))''',
        'CREATE VIRTUAL TABLE IF NOT EXISTS ai_rag_chunks_fts USING fts5(chunk_id UNINDEXED,chat_id UNINDEXED,text,tokenize=unicode61)',
        '''CREATE TABLE IF NOT EXISTS ai_rag_queries(task_id INTEGER PRIMARY KEY,state TEXT NOT NULL,
           query TEXT,token TEXT,lease_until TEXT,created_at TEXT,finished_at TEXT,result_json TEXT,error TEXT)''',
        '''CREATE TABLE IF NOT EXISTS ai_rag_usage(id INTEGER PRIMARY KEY,at TEXT NOT NULL,purpose TEXT NOT NULL,
           tokens INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'reserved')''',
        'CREATE INDEX IF NOT EXISTS idx_rag_usage_at ON ai_rag_usage(at)',
        'CREATE TABLE IF NOT EXISTS ai_rag_cache(key TEXT PRIMARY KEY,vector_json TEXT NOT NULL,expires_at TEXT NOT NULL)',
        '''CREATE TABLE IF NOT EXISTS ai_rag_build_members(chat_id INTEGER,day TEXT,generation TEXT,
           chunk_id TEXT,PRIMARY KEY(chat_id,day,generation,chunk_id))''',
        '''CREATE TABLE IF NOT EXISTS ai_rag_events(id INTEGER PRIMARY KEY,chat_id INTEGER,message_id INTEGER,
           day TEXT,kind TEXT NOT NULL)''',
        'CREATE INDEX IF NOT EXISTS idx_rag_events_message ON ai_rag_events(chat_id,message_id,id)',
    ]
    for sql in statements:
        conn.execute(sql)
    if 'build_revision' not in {r[1] for r in conn.execute('PRAGMA table_info(ai_rag_days)')}:
        conn.execute('ALTER TABLE ai_rag_days ADD COLUMN build_revision INTEGER')
    if exists(conn, 'ai_tasks'):
        columns = {r[1] for r in conn.execute('PRAGMA table_info(ai_tasks)')}
        for name in ('rag_state', 'rag_deadline_at'):
            if name not in columns:
                conn.execute(f'ALTER TABLE ai_tasks ADD COLUMN {name} TEXT')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_rag_task_state ON ai_tasks(rag_state,status,rag_deadline_at)')
    from mechanics import ensure_schema as mechanics_schema
    mechanics_schema(conn)
    from rag_counters import ensure as ensure_counters
    ensure_counters(conn)
    if exists(conn, 'messages_reactions'):
        conn.execute('CREATE INDEX IF NOT EXISTS idx_rag_source_day ON messages_reactions(chat_id,substr(date,1,10))')
        for suffix, event, condition, refs in [
            ('insert', 'INSERT', '', ['NEW']),
            ('delete', 'DELETE', '', ['OLD']),
            ('update', 'UPDATE OF message_text,date,user_id,chat_id,message_id',
             'WHEN OLD.message_text IS NOT NEW.message_text OR OLD.date IS NOT NEW.date OR OLD.user_id IS NOT NEW.user_id OR OLD.chat_id IS NOT NEW.chat_id OR OLD.message_id IS NOT NEW.message_id', ['OLD','NEW']),
        ]:
            inserts = ' '.join(f"INSERT INTO ai_rag_events(chat_id,message_id,day,kind) VALUES ({ref}.chat_id,{ref}.message_id,substr({ref}.date,1,10),'{suffix}');" for ref in refs)
            conn.execute(f'CREATE TRIGGER IF NOT EXISTS ai_rag_message_{suffix} AFTER {event} ON messages_reactions {condition} BEGIN {inserts} END')


def initialize():
    with closing(connect()) as conn, conn:
        ensure_schema(conn)


def get_state(conn, key, default=None):
    row = conn.execute('SELECT value FROM ai_rag_state WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def set_state(conn, key, value):
    conn.execute('INSERT INTO ai_rag_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))


def eligible(conn, row, modes=None):
    from ai_runtime import mode
    if row['chat_id'] >= 0:
        return False, 'private'
    if (modes.get(row['chat_id'],'local') if modes is not None else mode(row['chat_id'], conn)) == 'off':
        return False, 'ai_off'
    text = (row['message_text'] or '').strip()
    if not text:
        return False, 'empty'
    if text.startswith('/'):
        return False, 'command'
    try:
        aware(row['date'])
    except (ValueError, TypeError):
        return False, 'invalid_date'
    return True, ''


def track_message(conn, row, initial=False, modes=None):
    ok, reason = eligible(conn, row, modes)
    day = str(row['date'] or '')[:10]
    old = conn.execute('SELECT * FROM ai_rag_message_status WHERE chat_id=? AND message_id=?', (row['chat_id'], row['message_id'])).fetchone()
    hash_value = message_hash(row)
    unchanged = old and old['source_hash'] == hash_value and old['eligible'] == int(ok)
    conn.execute('''INSERT INTO ai_rag_message_status VALUES (?,?,?,?,?,?,?,0)
        ON CONFLICT(chat_id,message_id) DO UPDATE SET day=excluded.day,source_hash=excluded.source_hash,
        eligible=excluded.eligible,reason=excluded.reason,indexed=CASE WHEN ? THEN indexed ELSE 0 END''',
        (row['chat_id'],row['message_id'],day,hash_value,int(ok),reason,int(initial),int(bool(unchanged))))
    return day, not unchanged


def dirty(conn, chat, day):
    if chat >= 0 or not day:
        return
    conn.execute('''INSERT INTO ai_rag_days(chat_id,day,updated_at) VALUES (?,?,?)
        ON CONFLICT(chat_id,day) DO UPDATE SET revision=revision+1,status=CASE WHEN status='uploading' THEN status ELSE 'pending' END,retry_at=NULL,updated_at=excluded.updated_at''', (chat,day,stamp()))


def bootstrap():
    """Seed a fixed rowid snapshot in bounded transactions, resumable after a crash."""
    with closing(connect()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        if get_state(conn,'bootstrapped'):
            return True
        if not exists(conn,'messages_reactions'):
            raise RuntimeError('Source message table is missing')
        cutoff = get_state(conn,'bootstrap_cutoff')
        if cutoff is None:
            cutoff=conn.execute('SELECT coalesce(max(rowid),0) FROM messages_reactions').fetchone()[0]
            set_state(conn,'bootstrap_cutoff',cutoff)
            set_state(conn,'bootstrap_cursor',0)
            set_state(conn,'total_source',conn.execute('SELECT count(*) FROM messages_reactions').fetchone()[0])
            conn.execute("INSERT INTO ai_rag_runs(kind,state,started_at,updated_at) VALUES ('initial','preparing',?,?)",(stamp(),stamp()))
        cursor=int(get_state(conn,'bootstrap_cursor','0'))
        rows=conn.execute('SELECT rowid source_rowid,chat_id,message_id,user_id,date,message_text FROM messages_reactions WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?',(cursor,int(cutoff),BOOTSTRAP_BATCH)).fetchall()
        modes=dict(conn.execute("SELECT chat_id,value FROM settings WHERE name='ai_source'"))
        for row in rows:
            day, _ = track_message(conn,row,initial=True,modes=modes)
            if eligible(conn,row,modes)[0]:
                conn.execute('INSERT OR IGNORE INTO ai_rag_days(chat_id,day,updated_at) VALUES (?,?,?)', (row['chat_id'],day,stamp()))
        if rows:
            set_state(conn,'bootstrap_cursor',rows[-1]['source_rowid'])
            set_state(conn,'service_state','preparing')
            set_state(conn,'heartbeat',stamp())
        previous_seed=get_state(conn,'seeded_count')
        seeded=int(previous_seed)+len(rows) if previous_seed is not None else conn.execute('SELECT count(*) FROM ai_rag_message_status').fetchone()[0]
        set_state(conn,'seeded_count',seeded)
        if len(rows)==BOOTSTRAP_BATCH:
            # A cheap seed progress report; full aggregation waits for completion.
            set_state(conn,'stats',encode(dict(total_db_messages=int(get_state(conn,'total_source','0')),eligible_messages=0,indexed_messages=0,pending_messages=0,excluded_messages=0,new_since_start=0,percent=0,chats=[],seeded_messages=seeded,updated_at=stamp(),model=MODEL,index_version=VERSION)))
            return False
        set_state(conn,'bootstrapped',stamp())
        set_state(conn,'enabled','1')
        set_state(conn,'mode_values',encode(modes))
        set_state(conn,'modes',digest([tuple(row) for row in conn.execute("SELECT chat_id,value FROM settings WHERE name='ai_source'")]))
        refresh_stats(conn)
        return True


def reconcile(limit=500, through=None):
    with closing(connect()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        events = conn.execute('SELECT * FROM ai_rag_events WHERE id<=? ORDER BY id LIMIT ?', (through if through is not None else 9223372036854775807,limit)).fetchall()
        for event in events:
            if through is not None and conn.execute('SELECT 1 FROM ai_rag_events WHERE chat_id=? AND message_id=? AND id>? LIMIT 1',(event['chat_id'],event['message_id'],through)).fetchone():
                conn.execute('DELETE FROM ai_rag_events WHERE id=?',(event['id'],))
                continue
            row = conn.execute('SELECT chat_id,message_id,user_id,date,message_text FROM messages_reactions WHERE chat_id=? AND message_id=?', (event['chat_id'],event['message_id'])).fetchone()
            dirty(conn,event['chat_id'],event['day'])
            if row:
                day, _ = track_message(conn,row)
                if day != event['day']: dirty(conn,row['chat_id'],day)
            else:
                conn.execute("UPDATE ai_rag_message_status SET eligible=0,reason='deleted',indexed=0 WHERE chat_id=? AND message_id=?", (event['chat_id'],event['message_id']))
            conn.execute('DELETE FROM ai_rag_events WHERE id=?',(event['id'],))
        # Source mode changes are not message edits. Re-evaluate only affected chats.
        settings = list(conn.execute("SELECT chat_id,value FROM settings WHERE name='ai_source'"))
        signature = digest([tuple(row) for row in settings])
        if get_state(conn,'modes') != signature:
            previous=json.loads(get_state(conn,'mode_values','{}'))
            current={str(row[0]):row[1] for row in settings}
            pending=json.loads(get_state(conn,'mode_refresh','{}'))
            chats = [row[0] for row in conn.execute('SELECT DISTINCT chat_id FROM ai_rag_message_status WHERE chat_id<0') if previous.get(str(row[0]),'local')!=current.get(str(row[0]),'local')]
            for chat in chats:
                # Eligibility of unchanged text is already recorded: no source scan is needed.
                if current.get(str(chat),'local')=='off':
                    conn.execute("UPDATE ai_rag_message_status SET eligible=0,indexed=0,reason='ai_off' WHERE chat_id=? AND eligible=1",(chat,))
                    pending.pop(str(chat),None)
                else:
                    pending[str(chat)]=0
                for row in conn.execute('SELECT DISTINCT day FROM ai_rag_message_status WHERE chat_id=?',(chat,)).fetchall(): dirty(conn,chat,row[0])
            set_state(conn,'modes',signature)
            set_state(conn,'mode_values',encode(current))
            set_state(conn,'mode_refresh',encode(pending))
            set_state(conn,'modes_changed','1')
        pending=json.loads(get_state(conn,'mode_refresh','{}'))
        if pending:
            chat=next(iter(pending))
            rows=conn.execute('SELECT rowid source_rowid,chat_id,message_id,user_id,date,message_text FROM messages_reactions WHERE chat_id=? AND rowid>? ORDER BY rowid LIMIT 500',(int(chat),pending[chat])).fetchall()
            modes={int(row[0]):row[1] for row in settings}
            days=set()
            for row in rows:
                day,changed=track_message(conn,row,modes=modes)
                if changed: days.add(day)
            for day in days: dirty(conn,int(chat),day)
            if len(rows)==500: pending[chat]=rows[-1]['source_rowid']
            else: pending.pop(chat)
            set_state(conn,'mode_refresh',encode(pending))
            set_state(conn,'modes_changed','1')
        if events or get_state(conn,'stats') is None or get_state(conn,'modes_changed')=='1':
            refresh_stats(conn)
            set_state(conn,'modes_changed','0')
        return len(events)


def refresh_stats(conn):
    from rag_counters import rows as counter_rows
    run = conn.execute('SELECT * FROM ai_rag_runs ORDER BY id DESC LIMIT 1').fetchone()
    initial = run is None or run['kind'] == 'initial'
    histogram=counter_rows(conn,'initial' if initial else 'all')
    all_rows=counter_rows(conn,'all')
    initial_rows=counter_rows(conn,'initial')
    counts=dict(total=sum(r['n'] for r in histogram),eligible=sum(r['n']*r['eligible'] for r in histogram),done_count=sum(r['n']*r['eligible']*r['indexed'] for r in histogram))
    total_now=sum(r['n'] for r in all_rows if r['reason']!='deleted')
    new_count=total_now-sum(r['n'] for r in initial_rows if r['reason']!='deleted')
    chats = []
    titles = {}
    if exists(conn,'web_chat_titles'):
        columns = {r[1] for r in conn.execute('PRAGMA table_info(web_chat_titles)')}
        if {'chat_id','title'} <= columns:
            titles = {r[0]:r[1] for r in conn.execute('SELECT chat_id,title FROM web_chat_titles')}
    grouped={};reasons={}
    for row in histogram:
        if row['chat_id']<0:
            bucket=grouped.setdefault(row['chat_id'],[0,0])
            bucket[0]+=row['n']*row['eligible'];bucket[1]+=row['n']*row['eligible']*row['indexed']
        if not row['eligible']:reasons[row['reason']]=reasons.get(row['reason'],0)+row['n']
    for chat,(eligible_count,done_count) in sorted(grouped.items()):
        chats.append(dict(chat_id=chat,title=titles.get(chat) or f'Чат {chat}',eligible_messages=eligible_count,indexed_messages=done_count))
    states={row['reason']:row['n'] for row in counter_rows(conn,'chunks')}
    data = dict(run_id=run['id'] if run else None,index_version=VERSION,model=MODEL,
                total_db_messages=total_now,eligible_messages=counts['eligible'],indexed_messages=counts['done_count'],
                excluded_messages=counts['total']-counts['eligible'],pending_messages=counts['eligible']-counts['done_count'],
                new_since_start=new_count,percent=100*counts['done_count']/counts['eligible'] if counts['eligible'] else 0,
                chats=chats,exclusions=reasons,chunks=states,updated_at=stamp(),started_at=run['started_at'] if run else None)
    set_state(conn,'stats',encode(data))
    if run:
        conn.execute('UPDATE ai_rag_runs SET stats_json=?,updated_at=? WHERE id=?',(encode(data),stamp(),run['id']))


def split_message(row):
    text = str(row['message_text']).strip()
    parts, offset = [], 0
    while offset < len(text):
        lo, hi = 1, min(len(text)-offset, 2000)
        while lo < hi:
            mid = (lo+hi+1)//2
            if tokens(text[offset:offset+mid]) <= 450: lo=mid
            else: hi=mid-1
        part = text[offset:offset+lo]
        parts.append(dict(message_id=row['message_id'],user_id=row['user_id'],date=aware(row['date']).isoformat(),
                          text=part,part=len(parts),source_hash=message_hash(row)))
        offset += lo
    return parts


def render_parts(parts):
    return '\n'.join(f"[{p['date']}] #{p['message_id']} user={p['user_id']}: {p['text']}" for p in parts)


def chunk_messages(rows):
    chunks, current = [], []
    for row in rows:
        for part in split_message(row):
            proposed = current+[part]
            gap = current and (aware(part['date'])-aware(current[-1]['date'])).total_seconds()>1800
            if current and (gap or len({p['message_id'] for p in proposed})>12 or tokens(render_parts(proposed))>600):
                chunks.append(current)
                overlap = [p for p in current if p['message_id']==current[-1]['message_id']]
                current = overlap if not gap and tokens(render_parts(overlap+[part]))<=600 and len({p['message_id'] for p in overlap+[part]})<=12 else []
            current.append(part)
    if current: chunks.append(current)
    return chunks


def plan_day(chat, day):
    with closing(connect()) as conn:
        job = conn.execute('SELECT * FROM ai_rag_days WHERE chat_id=? AND day=?',(chat,day)).fetchone()
        rows = conn.execute('''SELECT m.chat_id,m.message_id,m.user_id,m.date,m.message_text
            FROM messages_reactions m JOIN ai_rag_message_status s USING(chat_id,message_id)
            WHERE s.chat_id=? AND s.day=? AND s.eligible=1
            AND NOT EXISTS(SELECT 1 FROM ai_rag_events e WHERE e.chat_id=m.chat_id AND e.message_id=m.message_id)
            ORDER BY m.date,m.message_id''',(chat,day)).fetchall()
        active = conn.execute("SELECT * FROM ai_rag_chunks WHERE chat_id=? AND day=? AND state='active'",(chat,day)).fetchall()
    if job is None: return None
    hashes = {r['message_id']:message_hash(r) for r in rows}
    retained, covered = [], set()
    for chunk in active:
        parts = json.loads(chunk['parts_json'])
        if all(hashes.get(p['message_id'])==p['source_hash'] for p in parts):
            retained.append(chunk['id'])
            covered.update((p['message_id'],p['part'],p['source_hash']) for p in parts)
    # A long message is reusable only when every part still has valid coverage.
    missing = [r for r in rows if not all((p['message_id'],p['part'],p['source_hash']) in covered for p in split_message(r))]
    chunks = chunk_messages(missing)
    generation = digest([VERSION,chat,day,job['revision'],[message_hash(r) for r in rows]])
    with closing(connect()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT revision FROM ai_rag_days WHERE chat_id=? AND day=?',(chat,day)).fetchone()[0] != job['revision']:
            return None
        conn.executemany('INSERT OR IGNORE INTO ai_rag_build_members VALUES (?,?,?,?)',
                         [(chat,day,generation,ident) for ident in retained])
        for parts in chunks:
            text = render_parts(parts)
            ident = str(uuid.uuid5(uuid.NAMESPACE_URL, encode([VERSION,chat,day,generation,parts])))
            conn.execute('''INSERT OR IGNORE INTO ai_rag_chunks(id,chat_id,day,generation,min_id,max_id,start_at,end_at,text,content_hash,parts_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',(ident,chat,day,generation,min(p['message_id'] for p in parts),max(p['message_id'] for p in parts),parts[0]['date'],parts[-1]['date'],text,digest(parts),encode(parts)))
            conn.executemany('INSERT OR IGNORE INTO ai_rag_chunk_messages VALUES (?,?,?,?,?)',[(ident,chat,p['message_id'],p['part'],p['source_hash']) for p in parts])
        conn.execute("UPDATE ai_rag_days SET generation=?,build_revision=revision,status='uploading',updated_at=? WHERE chat_id=? AND day=?",(generation,stamp(),chat,day))
    return generation


def activate_day(chat, day, generation):
    with closing(connect()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        job = conn.execute('SELECT * FROM ai_rag_days WHERE chat_id=? AND day=?',(chat,day)).fetchone()
        if job is None or job['generation']!=generation or job['status']!='uploading': return False
        chunks = conn.execute('SELECT * FROM ai_rag_chunks WHERE chat_id=? AND day=? AND generation=?',(chat,day,generation)).fetchall()
        if any(c['state'] not in ('uploaded','active') for c in chunks): return False
        retained = conn.execute('''SELECT c.* FROM ai_rag_chunks c JOIN ai_rag_build_members b ON b.chunk_id=c.id
            WHERE b.chat_id=? AND b.day=? AND b.generation=? AND c.state='active' ''',(chat,day,generation)).fetchall()
        # Finish the immutable build even in a busy chat. New edits form the next
        # generation; only source hashes still current may count as covered.
        pending=job['build_revision']!=job['revision'] or bool(conn.execute('SELECT 1 FROM ai_rag_events WHERE chat_id=? AND day=? LIMIT 1',(chat,day)).fetchone())
        retained_ids = {c['id'] for c in retained}
        old_ids = [r[0] for r in conn.execute("SELECT id FROM ai_rag_chunks WHERE chat_id=? AND day=? AND generation<>?",(chat,day,generation)) if r[0] not in retained_ids]
        for ident in old_ids:
            conn.execute('DELETE FROM ai_rag_chunks_fts WHERE chunk_id=?',(ident,))
            conn.execute("UPDATE ai_rag_chunks SET state='obsolete' WHERE id=?",(ident,))
        for chunk in chunks:
            conn.execute("UPDATE ai_rag_chunks SET state='active',vector_json=NULL WHERE id=?",(chunk['id'],))
            conn.execute('DELETE FROM ai_rag_chunks_fts WHERE chunk_id=?',(chunk['id'],))
            conn.execute('INSERT INTO ai_rag_chunks_fts VALUES (?,?,?)',(chunk['id'],chat,chunk['text']))
        previously_indexed = {r[0] for r in conn.execute('SELECT message_id FROM ai_rag_message_status WHERE chat_id=? AND day=? AND indexed=1',(chat,day))}
        conn.execute('UPDATE ai_rag_message_status SET indexed=0 WHERE chat_id=? AND day=?',(chat,day))
        sources={p['message_id']:p['source_hash'] for c in list(chunks)+list(retained) for p in json.loads(c['parts_json'])}
        completed=0
        for mid,hash_value in sources.items():
            row=conn.execute('SELECT chat_id,message_id,user_id,date,message_text FROM messages_reactions WHERE chat_id=? AND message_id=?',(chat,mid)).fetchone()
            if row and message_hash(row)==hash_value and eligible(conn,row)[0]:
                updated=conn.execute('UPDATE ai_rag_message_status SET indexed=1 WHERE chat_id=? AND message_id=? AND source_hash=? AND eligible=1',(chat,mid,hash_value)).rowcount
                if mid not in previously_indexed: completed+=updated
        conn.execute('DELETE FROM ai_rag_build_members WHERE chat_id=? AND day=?',(chat,day))
        conn.execute("UPDATE ai_rag_days SET status=?,active_generation=?,retry_at=NULL,updated_at=? WHERE chat_id=? AND day=?",('pending' if pending else 'done',generation,stamp(),chat,day))
        set_state(conn,'last_success_at',stamp())
        conn.execute('INSERT INTO ai_rag_state(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('success:'+stamp(),str(completed)))
        refresh_stats(conn)
        return True


def snapshot(path=None):
    with closing(connect(path)) as conn:
        if not exists(conn,'ai_rag_state'): return dict(state='stopped',percent=0,total_db_messages=0,eligible_messages=0,indexed_messages=0,pending_messages=0,excluded_messages=0,new_since_start=0,chats=[])
        data = json.loads(get_state(conn,'stats','{}'))
        data.setdefault('percent',0)
        for key in ('heartbeat','last_success_at','last_upload_at','next_retry_at','error','service_state'):
            data[key]=get_state(conn,key)
        data['state']=data.pop('service_state') or 'stopped'
        if not get_state(conn,'enabled')=='1' and data['state']!='preparing': data['state']='stopped'
        heartbeat=data.get('heartbeat')
        if heartbeat and heartbeat < stamp(-30): data['state']='stopped'
        data['quota']=json.loads(get_state(conn,'quota','{}'))
        from mechanics import snapshot as mechanics_snapshot
        data['mechanics']=mechanics_snapshot(conn)
        start=stamp(-900)
        successes=conn.execute("SELECT value FROM ai_rag_state WHERE key LIKE 'success:%' AND substr(key,9)>=?",(start,)).fetchall()
        data['messages_per_minute']=sum(int(r[0]) for r in successes)/15
        local=datetime.now(ZONE)
        next_night=local.replace(hour=4,minute=0,second=0,microsecond=0)
        if next_night<=local: next_night+=timedelta(days=1)
        data['next_night_at']=next_night.isoformat()
        data['night_event_cutoff']=get_state(conn,'night_event_cutoff')
        return data
