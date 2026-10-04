"""Independent RAG service: fenced retrieval and resumable free-tier indexing."""
from __future__ import annotations

import json
import logging
import signal
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime
import rag_repository as repo
from rag_embedding import Qdrant, Unavailable, embed
import rag_search
import mechanics

STOP=threading.Event()
READY=threading.Event()


def write_status(**values):
    """Observability is best effort: a busy DB must never terminate a worker."""
    try:
        with closing(repo.connect()) as conn, conn:
            for key,value in values.items(): repo.set_state(conn,key,value)
        return True
    except sqlite3.OperationalError:
        logging.warning('RAG status write deferred: SQLite temporarily unavailable')
        return False


def report(state, error='', wait=0):
    return write_status(heartbeat=repo.stamp(),service_state=state,error=error,
                        next_retry_at=repo.stamp(wait) if wait else '')


def supervise(threads):
    failed=[thread.name for thread in threads if not thread.is_alive()]
    if failed:
        raise RuntimeError('RAG worker terminated: '+', '.join(failed))
    # Independent pulse also covers long tokenization/planning steps.
    write_status(heartbeat=repo.stamp())


def retrieval_loop():
    while not STOP.is_set():
        try:
            READY.clear()
            task=rag_search.claim()
            if task: rag_search.prepare(task)
            else: READY.wait(25)
        except Exception:
            logging.exception('RAG context preparation failed')
            STOP.wait(1)


def initial_pending(conn):
    run=conn.execute("SELECT finished_at FROM ai_rag_runs WHERE kind='initial' ORDER BY id DESC LIMIT 1").fetchone()
    if run and run[0]: return False
    return conn.execute('SELECT 1 FROM ai_rag_message_status WHERE initial=1 AND eligible=1 AND indexed=0 LIMIT 1').fetchone() is not None


def pick_day():
    from ai_runtime import mode
    with closing(repo.connect()) as conn:
        last=repo.get_state(conn,'last_chat')
        # Fresh updates have priority, but every fourth confirmed upload serves
        # unfinished initial history so an active chat cannot starve backfill.
        backfill=int(repo.get_state(conn,'index_steps','0'))%4==3 and initial_pending(conn)
        # Materialize pending days once. A correlated EXISTS can otherwise scan
        # the entire pending history again for every completed/empty day.
        extra=" AND (chat_id,day) IN (SELECT chat_id,day FROM ai_rag_message_status WHERE initial=1 AND eligible=1 AND indexed=0 GROUP BY chat_id,day)" if backfill else ''
        rows=conn.execute("SELECT * FROM ai_rag_days d WHERE status<>'done' AND (retry_at IS NULL OR retry_at<=?) AND NOT EXISTS(SELECT 1 FROM ai_rag_events e WHERE e.chat_id=d.chat_id AND e.day=d.day)"+extra+" ORDER BY day DESC",(repo.stamp(),)).fetchall()
        # Latest dirty day per eligible chat, rotating the most recently visited chat last.
        choices={}
        for row in rows:
            if row['chat_id'] not in choices and mode(row['chat_id'],conn)!='off': choices[row['chat_id']]=dict(row)
        if not choices: return None
        options=list(choices.values())
        return next((r for r in options if str(r['chat_id'])!=last),options[0])


def index_once(client):
    job=pick_day()
    if not job: return False
    chat,day=job['chat_id'],job['day']
    generation=job['generation'] if job['status']=='uploading' else repo.plan_day(chat,day)
    if generation is None: return True
    # A day is resumed after each confirmed point; do not hold a DB lock across HTTP.
    with closing(repo.connect()) as conn:
        chunk=conn.execute("SELECT * FROM ai_rag_chunks WHERE chat_id=? AND day=? AND generation=? AND state IN ('prepared','embedded') ORDER BY min_id,id LIMIT 1",(chat,day,generation)).fetchone()
    if chunk:
        chunk=dict(chunk)
        if chunk['vector_json']: vector=json.loads(chunk['vector_json'])
        else:
            vector=embed(chunk['text'],purpose='index',timeout=15)
            with closing(repo.connect()) as conn, conn:
                conn.execute("UPDATE ai_rag_chunks SET vector_json=?,state='embedded' WHERE id=? AND state='prepared'",(repo.encode(vector),chunk['id']))
        client.upsert(chunk,vector)
        with closing(repo.connect()) as conn, conn:
            conn.execute("UPDATE ai_rag_chunks SET state='uploaded' WHERE id=? AND state IN ('prepared','embedded')",(chunk['id'],))
            repo.set_state(conn,'last_chat',chat)
            repo.set_state(conn,'index_steps',int(repo.get_state(conn,'index_steps','0'))+1)
            repo.set_state(conn,'last_upload_at',repo.stamp())
        return True
    repo.activate_day(chat,day,generation)
    return True


def cleanup(client):
    with closing(repo.connect()) as conn:
        ids=[r[0] for r in conn.execute("SELECT id FROM ai_rag_chunks WHERE state='obsolete' LIMIT 100")]
    if ids:
        client.delete(ids)
        with closing(repo.connect()) as conn, conn:
            for ident in ids:
                conn.execute('DELETE FROM ai_rag_chunk_messages WHERE chunk_id=?',(ident,))
                conn.execute("DELETE FROM ai_rag_chunks WHERE id=? AND state='obsolete'",(ident,))
    return bool(ids)


def maintenance():
    with closing(repo.connect()) as conn, conn:
        conn.execute('DELETE FROM ai_rag_cache WHERE expires_at<?',(repo.stamp(),))
        conn.execute('DELETE FROM ai_rag_usage WHERE at<?',(repo.stamp(-3*86400),))
        conn.execute("DELETE FROM ai_rag_state WHERE key LIKE 'success:%' AND substr(key,9)<?",(repo.stamp(-3600),))
        last=repo.get_state(conn,'stats_rebuilt_at')
        if not last or last<repo.stamp(-7*86400):
            from rag_counters import rebuild
            rebuild(conn)
            repo.set_state(conn,'stats_rebuilt_at',repo.stamp())
        repo.refresh_stats(conn)


def night_window(local):
    return 4 <= local.hour < 7


def night_cutoff(conn, local):
    """Keep an interrupted snapshot across restarts and subsequent nights."""
    if repo.get_state(conn,'night_open')!='1':
        if repo.get_state(conn,'night_finished_day')==local.date().isoformat():
            return None
        cutoff=conn.execute('SELECT coalesce(max(id),0) FROM ai_rag_events').fetchone()[0]
        repo.set_state(conn,'night_event_cutoff',cutoff)
        repo.set_state(conn,'night_open','1')
    return int(repo.get_state(conn,'night_event_cutoff','0'))


def indexing_loop():
    client=Qdrant();remote_ready=False;retry_at=0;last_maintenance=0
    handbook=Qdrant(mechanics.COLLECTION);handbook_ready=False;last_sync=0
    while not STOP.is_set():
        handbook_step=False
        try:
            if not last_sync:
                handbook_step=True
                mechanics.sync();last_sync=time.monotonic()
                handbook_step=False
            local=datetime.now(repo.ZONE)
            if not night_window(local):
                with closing(repo.connect()) as conn:
                    pending=repo.get_state(conn,'night_open')=='1'
                report('night_paused' if pending else 'night_waiting')
                STOP.wait(60)
                continue
            with closing(repo.connect()) as conn, conn:
                cutoff=night_cutoff(conn,local)
            if cutoff is None:
                report('night_waiting');STOP.wait(60);continue
            with closing(repo.connect()) as conn:
                last=repo.get_state(conn,'mechanics_checked_at')
            if not last or last<repo.stamp(-2*86400):
                handbook_step=True
                mechanics.sync()
                write_status(mechanics_checked_at=repo.stamp())
                handbook_step=False
            if not repo.bootstrap():
                STOP.wait(.15)
                continue
            repo.reconcile(through=cutoff)
            with closing(repo.connect()) as conn:
                more=conn.execute('SELECT 1 FROM ai_rag_events WHERE id<=? LIMIT 1',(cutoff,)).fetchone()
            if more:
                STOP.wait(.1);continue
            if time.monotonic()-last_maintenance>60:
                maintenance();last_maintenance=time.monotonic()
            with closing(repo.connect()) as conn:
                initial=initial_pending(conn)
                enabled=repo.get_state(conn,'enabled')=='1'
            if not initial:
                with closing(repo.connect()) as conn, conn:
                    conn.execute("UPDATE ai_rag_runs SET state='completed',finished_at=coalesce(finished_at,?) WHERE kind='initial'",(repo.stamp(),))
            if not enabled:
                report('stopped');STOP.wait(5);continue
            if time.monotonic()<retry_at:
                with closing(repo.connect()) as conn, conn: repo.set_state(conn,'heartbeat',repo.stamp())
                STOP.wait(min(5,retry_at-time.monotonic()));continue
            with closing(repo.connect()) as conn:
                pending_docs=conn.execute("SELECT 1 FROM ai_mechanics_sections WHERE current=1 AND state<>'active' LIMIT 1").fetchone()
                deleting_docs=conn.execute('SELECT 1 FROM ai_mechanics_sections WHERE current=0 LIMIT 1').fetchone()
            if pending_docs:
                handbook_step=True
                if not handbook_ready:handbook.ensure();handbook_ready=True
                write_status(mechanics_state='indexing',mechanics_error='',mechanics_retry_at='')
                mechanics.index_once(handbook)
                report('indexing' if initial else 'night')
                continue
            if deleting_docs:
                handbook_step=True
                if not handbook_ready:handbook.ensure();handbook_ready=True
                mechanics.cleanup(handbook)
                handbook_step=False
            write_status(mechanics_state='completed',mechanics_error='',mechanics_retry_at='')
            local=datetime.now(repo.ZONE)
            if not night_window(local):
                report('completed')
                with closing(repo.connect()) as conn, conn:
                    conn.execute("UPDATE ai_rag_runs SET state='completed',finished_at=coalesce(finished_at,?) WHERE kind='initial'",(repo.stamp(),))
                STOP.wait(5);continue
            if not remote_ready:
                client.ensure();remote_ready=True
            if not initial:
                with closing(repo.connect()) as conn, conn:
                    run=conn.execute('SELECT * FROM ai_rag_runs ORDER BY id DESC LIMIT 1').fetchone()
                    if run is None or run['kind']=='initial' or repo.aware(run['started_at']+'Z').date()!=local.date():
                        conn.execute("INSERT INTO ai_rag_runs(kind,state,started_at,updated_at) VALUES ('night','indexing',?,?)",(repo.stamp(),repo.stamp()))
                        repo.refresh_stats(conn)
            report('night')
            if not index_once(client):
                if cleanup(client):
                    continue
                with closing(repo.connect()) as conn, conn:
                    repo.set_state(conn,'night_open','0')
                    repo.set_state(conn,'night_finished_day',local.date().isoformat())
                STOP.wait(60)
            elif int(time.monotonic())%20==0: cleanup(client)
        except Unavailable as exc:
            if handbook_step:write_status(mechanics_state='waiting',mechanics_error=str(exc),mechanics_retry_at=repo.stamp(exc.retry_after))
            report('waiting',str(exc),exc.retry_after)
            retry_at=time.monotonic()+exc.retry_after
            STOP.wait(1)
        except Exception:
            logging.exception('RAG indexing step failed')
            report('error','Ошибка шага индексации; автоматический повтор через 30 секунд',30)
            if handbook_step:write_status(mechanics_state='error',mechanics_error='Ошибка шага справочника; автоматический повтор')
            retry_at=time.monotonic()+30
            STOP.wait(1)


def main():
    import ai_runtime
    ai_runtime.initialize();repo.initialize()
    # The unit starts one process; a local file lock also prevents accidental duplicates.
    lock=None
    try:
        import fcntl
        lock=open('.rag-service.lock','w')
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except ImportError: pass
    for signum in (signal.SIGINT,signal.SIGTERM): signal.signal(signum,lambda *_:STOP.set())
    logging.basicConfig(level=logging.INFO)
    from ai_notifications import rag_listener
    threading.Thread(target=rag_listener,args=(STOP,READY),daemon=True).start()
    threads=[threading.Thread(target=retrieval_loop,name=f'rag-search-{i}') for i in range(2)]
    threads.append(threading.Thread(target=indexing_loop,name='rag-index'))
    for thread in threads: thread.start()
    try:
        while not STOP.wait(5): supervise(threads)
    finally:
        STOP.set()
        READY.set()
        for thread in threads: thread.join(25)
        report('stopped')
        if lock: lock.close()


if __name__=='__main__': main()
