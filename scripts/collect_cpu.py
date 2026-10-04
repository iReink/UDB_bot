"""24-hour CPU study: aggregate counters and work volumes, no message contents."""
import argparse
import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from measure_cpu import measure


def workload(path,start):
    with closing(sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro',uri=True,timeout=10)) as conn:
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result={}
        local=datetime.fromisoformat(start).astimezone(ZoneInfo('Asia/Yekaterinburg')).replace(tzinfo=None).isoformat()
        if 'messages_reactions' in tables:
            result['messages_since_start']=conn.execute('SELECT count(*) FROM messages_reactions WHERE date>=?',(local,)).fetchone()[0]
        utc=datetime.fromisoformat(start).replace(tzinfo=None).isoformat()
        for table in ('ai_tasks','ai_type_checks','ai_search_plans'):
            if table in tables:
                result[table]=dict(conn.execute(f'SELECT status,count(*) FROM {table} WHERE created_at>=? GROUP BY status',(utc,)))
        if 'ai_rag_state' in tables:
            result['confirmed_index_steps']=int((conn.execute("SELECT value FROM ai_rag_state WHERE key='index_steps'").fetchone() or [0])[0])
            result['rag_state']=(conn.execute("SELECT value FROM ai_rag_state WHERE key='service_state'").fetchone() or ['unknown'])[0]
        if 'ai_tasks' in tables:
            delays=[r[0] for r in conn.execute("SELECT (julianday(finished_at)-julianday(created_at))*86400 FROM ai_tasks WHERE created_at>=? AND finished_at IS NOT NULL AND task_type NOT IN ('profile_update','chat_summary')",(utc,)) if r[0] is not None]
            delays.sort()
            result['request_completion_seconds']=dict(count=len(delays),median=delays[len(delays)//2] if delays else None,p95=delays[min(len(delays)-1,int(len(delays)*.95))] if delays else None)
        return result


def collect(db,output,hours):
    start=datetime.now(timezone.utc).isoformat();end=time.monotonic()+hours*3600
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('a',encoding='utf-8') as stream:
        def emit(value):stream.write(json.dumps(value,ensure_ascii=False)+'\n');stream.flush()
        emit(dict(event='start',at=start,workload=workload(db,start)))
        while time.monotonic()<end:
            result=measure(min(60,max(1,end-time.monotonic())))
            emit(result)
        emit(dict(event='end',at=datetime.now(timezone.utc).isoformat(),workload=workload(db,start)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=Path(__file__).resolve().parents[1]/'stats.db')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--hours',type=float,default=24)
    args=parser.parse_args()
    if not 0<args.hours<=48:parser.error('hours must be 0..48')
    collect(args.db,args.output,args.hours)
