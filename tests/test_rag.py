"""RAG contract tests: temporary source DB, fake external services, no real chats."""
import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch,Mock

from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
import ai_tasks
import ai_runtime as runtime
import rag_repository as repo
import rag_search as search
import rag_service as service
import rag_embedding as embedding


class RagTests(unittest.TestCase):
    def test_night_boundaries_and_interrupted_snapshot(self):
        for hour, expected in ((3,False),(4,True),(6,True),(7,False)):
            self.assertEqual(service.night_window(datetime(2026,10,5,hour)),expected)
        self.execute("UPDATE messages_reactions SET message_text='first edit' WHERE message_id=1 AND chat_id=-42")
        with closing(repo.connect()) as conn,conn:
            cutoff=service.night_cutoff(conn,datetime(2026,10,5,4))
        self.execute("UPDATE messages_reactions SET message_text='later edit' WHERE message_id=1 AND chat_id=-42")
        with closing(repo.connect()) as conn,conn:
            self.assertEqual(service.night_cutoff(conn,datetime(2026,10,6,4)),cutoff)
        repo.reconcile(through=cutoff)
        with closing(repo.connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM ai_rag_events WHERE id<=?',(cutoff,)).fetchone()[0],0)
            self.assertGreater(conn.execute('SELECT count(*) FROM ai_rag_events WHERE id>?',(cutoff,)).fetchone()[0],0)
        self.assertNotEqual((service.pick_day() or {}).get('chat_id'),-42)

    def test_counter_rebuild_and_repeated_updates_agree(self):
        from rag_counters import rebuild
        self.ready()
        for _ in range(3):
            self.execute('UPDATE ai_rag_message_status SET indexed=indexed')
        with closing(repo.connect()) as conn,conn:
            before=[tuple(r) for r in conn.execute('SELECT * FROM ai_rag_counters WHERE n>0 ORDER BY scope,chat_id,reason,eligible,indexed')]
            rebuild(conn)
            after=[tuple(r) for r in conn.execute('SELECT * FROM ai_rag_counters WHERE n>0 ORDER BY scope,chat_id,reason,eligible,indexed')]
            self.assertEqual(before,after)
            trace=[];conn.set_trace_callback(trace.append)
            repo.refresh_stats(conn)
            self.assertFalse(any('FROM ai_rag_message_status' in sql for sql in trace))

    def test_append_reuses_confirmed_points_without_embedding_or_upsert(self):
        old=self.ready()[0]['id']
        self.execute("INSERT INTO messages_reactions VALUES(-42,3,1,'2026-09-01T10:02:00','Только новая реплика',0)")
        repo.reconcile();generation=repo.plan_day(-42,'2026-09-01')
        with closing(repo.connect()) as conn:
            new=list(conn.execute('SELECT * FROM ai_rag_chunks WHERE generation=?',(generation,)))
            self.assertEqual(len(new),1)
            self.assertEqual([p['message_id'] for p in json.loads(new[0]['parts_json'])],[3])
        client=Mock()
        job=dict(chat_id=-42,day='2026-09-01',status='uploading',generation=generation)
        with patch('rag_service.pick_day',return_value=job),patch('rag_service.embed',return_value=[.1]*768) as embed:
            service.index_once(client);service.index_once(client)
        embed.assert_called_once();client.upsert.assert_called_once()
        with closing(repo.connect()) as conn:
            self.assertEqual(conn.execute('SELECT state FROM ai_rag_chunks WHERE id=?',(old,)).fetchone()[0],'active')
            self.assertEqual(conn.execute('SELECT count(*) FROM ai_rag_chunks_fts WHERE chunk_id=?',(old,)).fetchone()[0],1)
        self.assertEqual(repo.snapshot()['indexed_messages'],2) # initial snapshot excludes new message

    def test_edit_and_delete_preserve_unaffected_fragment(self):
        self.execute("INSERT INTO messages_reactions VALUES(-42,3,1,'2026-09-01T12:00:00','Отдельная тема',0)")
        repo.reconcile();old=self.ready()
        unchanged=next(c['id'] for c in old if c['min_id']==3)
        self.execute('DELETE FROM messages_reactions WHERE chat_id=-42 AND message_id=1')
        repo.reconcile();generation=repo.plan_day(-42,'2026-09-01')
        self.execute("UPDATE ai_rag_chunks SET state='uploaded' WHERE generation=?",(generation,))
        self.assertTrue(repo.activate_day(-42,'2026-09-01',generation))
        with closing(repo.connect()) as conn:
            self.assertEqual(conn.execute('SELECT state FROM ai_rag_chunks WHERE id=?',(unchanged,)).fetchone()[0],'active')
            active=list(conn.execute("SELECT parts_json FROM ai_rag_chunks WHERE state='active' AND chat_id=-42"))
            self.assertNotIn(1,[p['message_id'] for c in active for p in json.loads(c[0])])

    def test_document_cache_and_actual_tokens_avoid_repeated_requests(self):
        response=Mock(ok=True);response.json.return_value={'embedding':{'values':[.1]*768},'usageMetadata':{'promptTokenCount':12}}
        env={'GEMINI_API_KEY':'synthetic','RAG_EMBED_RPM':'100','RAG_EMBED_TPM':'30000','RAG_EMBED_RPD':'1000'}
        with patch.dict(os.environ,env),patch('ai_http.post',return_value=response) as post:
            embedding.embed('Документ','index');embedding.embed('Документ','index')
            post.assert_called_once()
            embedding.embed('Документ','query')
            embedding.embed('Изменённый документ','index')
            self.assertEqual(post.call_count,3)
        with closing(repo.connect()) as conn:
            self.assertEqual([r[0] for r in conn.execute('SELECT tokens FROM ai_rag_usage')],[12,12,12])

    def test_old_uploading_generation_remains_compatible(self):
        generation=repo.plan_day(-42,'2026-09-01')
        self.execute('DELETE FROM ai_rag_build_members')
        self.execute("UPDATE ai_rag_chunks SET state='uploaded' WHERE generation=?",(generation,))
        repo.initialize();repo.initialize()
        self.assertTrue(repo.activate_day(-42,'2026-09-01',generation))
        self.assertEqual(repo.snapshot()['indexed_messages'],2)

    def test_append_preserves_all_long_message_parts(self):
        self.execute("UPDATE messages_reactions SET message_text=? WHERE chat_id=-42 AND message_id=1",('Длинная история 🙂 '*500,))
        repo.reconcile();old={c['id'] for c in self.ready()}
        self.execute("INSERT INTO messages_reactions VALUES(-42,3,1,'2026-09-01T11:00:00','Дополнение',0)")
        repo.reconcile();generation=repo.plan_day(-42,'2026-09-01')
        self.execute("UPDATE ai_rag_chunks SET state='uploaded' WHERE generation=?",(generation,))
        self.assertTrue(repo.activate_day(-42,'2026-09-01',generation))
        with closing(repo.connect()) as conn:
            active={r[0] for r in conn.execute("SELECT id FROM ai_rag_chunks WHERE state='active' AND chat_id=-42")}
            self.assertTrue(old<=active)
            self.assertEqual(conn.execute('SELECT indexed FROM ai_rag_message_status WHERE chat_id=-42 AND message_id=1').fetchone()[0],1)

    def test_new_generation_arrival_keeps_retained_members_until_activation(self):
        old=self.ready()[0]['id']
        self.execute("INSERT INTO messages_reactions VALUES(-42,3,1,'2026-09-01T10:02:00','Третья реплика',0)")
        repo.reconcile();generation=repo.plan_day(-42,'2026-09-01')
        self.execute("INSERT INTO messages_reactions VALUES(-42,4,1,'2026-09-01T10:03:00','Четвёртая реплика',0)")
        repo.reconcile();repo.initialize()
        self.execute("UPDATE ai_rag_chunks SET state='uploaded' WHERE generation=?",(generation,))
        self.assertTrue(repo.activate_day(-42,'2026-09-01',generation))
        with closing(repo.connect()) as conn:
            self.assertEqual(conn.execute('SELECT state FROM ai_rag_chunks WHERE id=?',(old,)).fetchone()[0],'active')
            self.assertEqual(conn.execute('SELECT indexed FROM ai_rag_message_status WHERE chat_id=-42 AND message_id=4').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT status FROM ai_rag_days WHERE chat_id=-42').fetchone()[0],'pending')

    def test_expired_document_cache_requires_fresh_embedding(self):
        response=Mock(ok=True);response.json.return_value={'embedding':{'values':[.1]*768}}
        env={'GEMINI_API_KEY':'synthetic','RAG_EMBED_RPM':'100','RAG_EMBED_TPM':'30000','RAG_EMBED_RPD':'1000'}
        with patch.dict(os.environ,env),patch('ai_http.post',return_value=response) as post:
            embedding.embed('Документ','index')
            self.execute('UPDATE ai_rag_cache SET expires_at=?',(repo.stamp(-1),))
            embedding.embed('Документ','index')
            self.assertEqual(post.call_count,2)

    def test_status_lock_cannot_kill_error_recovery(self):
        with patch.object(repo,'connect',side_effect=sqlite3.OperationalError('database is locked')):
            self.assertFalse(service.report('error','Retry',30))
        self.assertTrue(service.report('indexing'))
        self.assertEqual(repo.snapshot()['state'],'indexing')

    def test_dead_indexer_is_detected_for_systemd_restart(self):
        live=Mock(name='live');live.name='rag-search';live.is_alive.return_value=True
        dead=Mock(name='dead');dead.name='rag-index';dead.is_alive.return_value=False
        with self.assertRaisesRegex(RuntimeError,'rag-index'):service.supervise([live,dead])

    def test_backfill_selection_does_not_rescan_history_for_each_day(self):
        with closing(repo.connect()) as conn,conn:
            conn.executemany("INSERT INTO ai_rag_message_status VALUES(-43,?,'2020-01-01','hash',1,'',1,0)",[(i,) for i in range(10,3010)])
            conn.executemany("INSERT INTO ai_rag_days(chat_id,day,updated_at) VALUES(-42,?,?)",[(f'empty-{i:03}',repo.stamp()) for i in range(250)])
            repo.set_state(conn,'index_steps','3')
        original=repo.connect
        def bounded(path=None):
            conn=original(path);remaining=[2000]
            def budget():
                remaining[0]-=1
                return int(remaining[0]<=0)
            conn.set_progress_handler(budget,100)
            return conn
        with patch.object(repo,'connect',side_effect=bounded):
            self.assertIsNotNone(service.pick_day())

    def test_supervisor_pulse_preserves_quota_wait_and_error(self):
        service.report('waiting','Minute quota',60)
        live=Mock();live.name='rag-index';live.is_alive.return_value=True
        service.supervise([live])
        result=repo.snapshot()
        self.assertEqual(result['state'],'waiting');self.assertEqual(result['error'],'Minute quota')
        self.assertTrue(result['next_retry_at'])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'test.db'
        self.db_patch=patch.object(ai_tasks,'DB_FILE',self.path);self.db_patch.start()
        runtime.initialize()
        with closing(repo.connect()) as conn,conn:
            conn.execute('CREATE TABLE messages_reactions(chat_id INTEGER,message_id INTEGER,user_id INTEGER,date TEXT,message_text TEXT,reactions_count INTEGER DEFAULT 0,PRIMARY KEY(chat_id,message_id))')
            conn.execute('CREATE TABLE users(chat_id INTEGER,user_id INTEGER,name TEXT,nick TEXT)')
            conn.execute("INSERT INTO users VALUES(-42,1,'Тестовый автор','@example')")
            conn.execute("INSERT INTO messages_reactions VALUES(-42,1,1,'2026-09-01T10:00:00','Резервный API включили, потому что локальная модель часто переставала отвечать.',0)")
            conn.execute("INSERT INTO messages_reactions VALUES(-42,2,1,'2026-09-01T10:01:00','Для надёжности выбрали переключение на внешнюю модель.',0)")
            conn.execute("INSERT INTO messages_reactions VALUES(-43,1,1,'2026-09-01T10:00:00','Секрет другого чата.',0)")
        repo.initialize();repo.bootstrap()
        runtime.heartbeat('pc','local',list(runtime.TABLES))

    def tearDown(self):
        self.db_patch.stop();self.tmp.cleanup()

    def execute(self,sql,args=()):
        with closing(repo.connect()) as conn,conn: return conn.execute(sql,args).lastrowid

    def ready(self,chat=-42):
        generation=repo.plan_day(chat,'2026-09-01')
        self.execute("UPDATE ai_rag_chunks SET state='uploaded' WHERE chat_id=? AND generation=?",(chat,generation))
        self.assertTrue(repo.activate_day(chat,'2026-09-01',generation))
        with closing(repo.connect()) as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM ai_rag_chunks WHERE chat_id=? AND state='active'",(chat,))]

    def task(self,text='Почему выбрали резервный API?',short=None):
        with patch.object(ai_tasks,'get_response_short_memory',return_value=short or []),patch.object(ai_tasks,'get_response_long_memory',return_value=[]),patch.object(ai_tasks,'get_latest_profile_json',return_value=None):
            return ai_tasks.create_response_task(chat_id=-42,requester_user_id=1,request_message_id=100,message_text=text,requester_name='Тест',requester_nick=None,trigger_reason='mention')

    def test_migration_repeat_and_legacy_generation(self):
        repo.initialize();repo.initialize()
        with closing(repo.connect()) as conn,conn:
            conn.execute("INSERT INTO ai_tasks(task_type,status,priority,model,prompt,payload_json,chat_id,user_id,request_message_id,created_at,updated_at) VALUES('response','pending',1,'local','legacy','{}',-42,1,5,?,?)",(repo.stamp(),repo.stamp()))
        self.assertEqual(runtime.claim('tasks','pc')['prompt'],'legacy')

    def test_bootstrap_is_fixed_and_reaction_changes_do_not_dirty(self):
        self.assertTrue(repo.bootstrap())
        self.execute('UPDATE messages_reactions SET reactions_count=10 WHERE chat_id=-42 AND message_id=1')
        self.assertEqual(repo.reconcile(),0)
        self.execute("INSERT INTO messages_reactions VALUES(-42,3,1,'2026-09-02T10:00:00','Новая запись после начального снимка.',0)")
        repo.reconcile()
        data=repo.snapshot()
        self.assertEqual(data['new_since_start'],1)
        self.assertEqual(data['eligible_messages'],3)
        self.assertEqual(data['total_db_messages'],4)

    def test_activation_waits_for_all_points_and_counts_unique_messages(self):
        generation=repo.plan_day(-42,'2026-09-01')
        self.assertFalse(repo.activate_day(-42,'2026-09-01',generation))
        self.assertEqual(repo.snapshot()['indexed_messages'],0)
        self.execute("UPDATE ai_rag_chunks SET state='uploaded' WHERE chat_id=-42")
        self.assertTrue(repo.activate_day(-42,'2026-09-01',generation))
        self.assertEqual(repo.snapshot()['indexed_messages'],2)
        repo.activate_day(-42,'2026-09-01',generation)
        self.assertEqual(repo.snapshot()['indexed_messages'],2)

    def test_edit_delete_and_ai_off_adjust_coverage(self):
        chunks=self.ready()
        self.execute("UPDATE messages_reactions SET message_text='Исправленный текст.' WHERE chat_id=-42 AND message_id=1")
        with closing(repo.connect()) as conn:
            self.assertIsNone(search.validate_chunk(conn,chunks[0]['id'],-42,100,set()))
        repo.reconcile();self.assertEqual(repo.snapshot()['indexed_messages'],1)
        self.execute('DELETE FROM messages_reactions WHERE chat_id=-42 AND message_id=2')
        repo.reconcile();self.assertEqual(repo.snapshot()['eligible_messages'],2)
        runtime.set_mode(-42,'off');repo.reconcile()
        self.assertEqual(repo.snapshot()['eligible_messages'],1)
        self.assertEqual(repo.snapshot()['indexed_messages'],0)

    def test_new_generation_keeps_old_until_confirmed_then_obsoletes(self):
        old=self.ready()[0]['id']
        self.execute("UPDATE messages_reactions SET message_text='Новое решение о резервном API.' WHERE chat_id=-42 AND message_id=1")
        repo.reconcile();generation=repo.plan_day(-42,'2026-09-01')
        self.assertFalse(repo.activate_day(-42,'2026-09-01',generation))
        self.execute("UPDATE ai_rag_chunks SET state='uploaded' WHERE generation=?",(generation,))
        self.assertTrue(repo.activate_day(-42,'2026-09-01',generation))
        with closing(repo.connect()) as conn:
            self.assertEqual(conn.execute('SELECT state FROM ai_rag_chunks WHERE id=?',(old,)).fetchone()[0],'obsolete')

    def test_partition_gap_overlap_and_long_unicode_parts(self):
        rows=[dict(chat_id=-42,message_id=i,user_id=1,date=f'2026-09-01T10:{i:02}:00',message_text='Обсуждаем архитектуру резервного API. '*12) for i in range(1,8)]
        chunks=repo.chunk_messages(rows)
        self.assertGreater(len(chunks),1)
        self.assertTrue(all(repo.tokens(repo.render_parts(c))<=600 for c in chunks))
        row=dict(rows[0],message_text='Очень длинное сообщение 🙂 '*300)
        parts=repo.split_message(row)
        self.assertEqual(''.join(p['text'] for p in parts),row['message_text'].strip())
        self.assertNotIn('�',''.join(p['text'] for p in parts))
        self.assertGreater(len(parts),1)
        gap=repo.chunk_messages([rows[0],dict(rows[1],date='2026-09-01T11:00:00')])
        self.assertEqual(len(gap),2)

    def test_resume_upsert_failure_does_not_repeat_embedding(self):
        client=Mock();client.upsert.side_effect=[embedding.Unavailable('temporary'),None]
        with patch('rag_service.embed',return_value=[.1]*768) as embed:
            with self.assertRaises(embedding.Unavailable):service.index_once(client)
            # Rotation would visit the other chat first; select the same day explicitly.
            with patch('rag_service.pick_day',return_value=dict(chat_id=-42,day='2026-09-01',status='uploading',generation=self.generation(-42))):
                service.index_once(client)
            embed.assert_called_once()

    def generation(self,chat):
        with closing(repo.connect()) as conn:
            return conn.execute('SELECT generation FROM ai_rag_days WHERE chat_id=?',(chat,)).fetchone()[0]

    def test_generation_is_not_claimable_until_ready(self):
        self.ready();ident=self.task()
        self.assertIsNone(runtime.claim('tasks','pc'))
        task=search.claim();self.assertEqual(task['id'],ident)
        with patch('rag_search.embed',side_effect=embedding.Unavailable('Google unavailable')):
            self.assertTrue(search.prepare(task))
        generation=runtime.claim('tasks','pc')
        self.assertIn('<rag_history>',generation['prompt'])
        self.assertEqual(generation['payload']['rag']['error'],'Google unavailable')

    def test_deadline_releases_original_prompt_and_fences_late_result(self):
        self.ready();ident=self.task();task=search.claim()
        self.execute('UPDATE ai_tasks SET rag_deadline_at=? WHERE id=?',(repo.stamp(-1),ident))
        original=runtime.claim('tasks','pc')
        self.assertNotIn('<rag_history>',original['prompt'])
        with patch('rag_search.retrieve',return_value=dict(query='q',fragments=[dict(text='late')],error=None)):
            self.assertFalse(search.prepare(task))
        self.assertEqual(ai_tasks.get_task(ident)['prompt'],original['prompt'])

    def test_future_cross_chat_and_short_context_excluded(self):
        chunks=self.ready();foreign=self.ready(-43)
        with closing(repo.connect()) as conn:
            self.assertIsNone(search.validate_chunk(conn,foreign[0]['id'],-42,100,set()))
            self.assertIsNone(search.validate_chunk(conn,chunks[0]['id'],-42,2,set()))
            result=search.validate_chunk(conn,chunks[0]['id'],-42,100,{1})
            self.assertEqual(result['message_ids'],[2])
            self.assertIsNone(search.validate_chunk(conn,chunks[0]['id'],-42,100,{1,2}))

    def test_empty_and_context_budget(self):
        self.ready();ident=self.task('Привет!')
        task=search.claim()
        with patch('rag_search.embed',return_value=[.1]*768),patch.object(embedding.Qdrant,'query',return_value=[]):
            result=search.retrieve(task,time.monotonic()+4)
        self.assertEqual(result['fragments'],[])

    def test_retry_reuses_snapshot_and_rag(self):
        self.ready();ident=self.task();task=search.claim()
        with patch('rag_search.embed',side_effect=embedding.Unavailable('offline')):search.prepare(task)
        original=json.loads(ai_tasks.get_task(ident)['payload_json'])['rag']
        with patch.object(ai_tasks,'get_response_short_memory',side_effect=AssertionError('must reuse')):
            ai_tasks.requeue_or_fail_response_task(ident,previous_response='bad',error_text='bad')
        current=ai_tasks.get_task(ident)
        self.assertEqual(json.loads(current['payload_json'])['rag'],original)
        self.assertIn('<rag_history>',current['prompt'])

    def test_provider_shortening_discards_rag_first(self):
        from ai_providers import prepare_prompt
        prompt='Обязательный вопрос.\n- [date] короткая память.'+search.context_block([dict(text='Длинная история. '*300)])
        result=prepare_prompt(dict(prompt=prompt,task_type='response'),limit=40)
        self.assertNotIn('<rag_history>',result)
        self.assertIn('короткая память',result)

    def test_query_lease_is_single_owner(self):
        self.task();first=search.claim();self.assertIsNotNone(first)
        self.assertIsNone(search.claim())

    def test_quota_keeps_daily_and_minute_budget_for_queries(self):
        with patch.dict(os.environ,{'RAG_EMBED_RPM':'100','RAG_EMBED_TPM':'30000','RAG_EMBED_RPD':'10'}):
            for _ in range(8):embedding.reserve('tiny','index')
            with self.assertRaises(embedding.Unavailable):embedding.reserve('tiny','index')
            self.assertIsNotNone(embedding.reserve('tiny','query'))
        with closing(repo.connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM ai_rag_usage').fetchone()[0],9)

    def test_daily_850_index_100_search_50_headroom(self):
        with closing(repo.connect()) as conn,conn:
            conn.executemany("INSERT INTO ai_rag_usage(at,purpose,tokens,status) VALUES (?,'index',1,'done')",[(repo.stamp(-180),)]*850)
        with patch.dict(os.environ,{'RAG_EMBED_RPM':'100','RAG_EMBED_TPM':'30000','RAG_EMBED_RPD':'1000'}):
            with self.assertRaisesRegex(embedding.Unavailable,'бюджет индексации'):
                embedding.reserve('tiny','index')
            embedding.reserve('tiny','query')
            with closing(repo.connect()) as conn,conn:
                conn.executemany("INSERT INTO ai_rag_usage(at,purpose,tokens,status) VALUES (?,'query',1,'done')",[(repo.stamp(-180),)]*99)
            with self.assertRaisesRegex(embedding.Unavailable,'Общий дневной'):
                embedding.reserve('tiny','query')
        self.assertEqual(repo.snapshot()['quota']['index_daily_limit'],850)

    def test_zero_quota_and_cache_are_safe(self):
        with patch.dict(os.environ,{'RAG_EMBED_RPM':'0','RAG_EMBED_TPM':'0','RAG_EMBED_RPD':'0'}):
            with self.assertRaises(embedding.Unavailable):embedding.reserve('tiny','index')

    def test_embedding_validates_dimension_and_keeps_key_out_of_database(self):
        response=Mock(ok=True);response.json.return_value={'embedding':{'values':[.1]*768}}
        with patch.dict(os.environ,{'GEMINI_API_KEY':'synthetic-secret','RAG_EMBED_RPM':'100','RAG_EMBED_TPM':'30000','RAG_EMBED_RPD':'1000'}),patch('ai_http.post',return_value=response) as post:
            self.assertEqual(len(embedding.embed('q')),768)
            embedding.embed('q');post.assert_called_once()
        with closing(repo.connect()) as conn:
            self.assertNotIn('synthetic-secret',str(list(conn.execute('SELECT * FROM ai_rag_cache'))))

    def test_qdrant_query_contains_mandatory_filters(self):
        client=embedding.Qdrant()
        with patch.object(client,'request',return_value={'points':[]}) as request:
            client.query([.1]*768,-42,100)
            body=request.call_args.args[2]
            self.assertEqual(body['filter']['must'][0]['match']['value'],-42)
            self.assertEqual(body['filter']['must'][2]['range']['lt'],100)

    def test_status_authorization_and_no_store(self):
        from web.rag_status import register
        app=FastAPI();templates=Mock()
        def auth(request):
            if not request.headers.get('x-user'):raise HTTPException(401)
            return {'telegram_user_id':int(request.headers['x-user'])}
        register(app,templates,self.path,auth,{1})
        client=TestClient(app)
        self.assertEqual(client.get('/api/rag/status').status_code,401)
        self.assertEqual(client.get('/api/rag/status',headers={'x-user':'2'}).status_code,403)
        result=client.get('/api/rag/status',headers={'x-user':'1'})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.headers['cache-control'],'no-store')
        self.assertNotIn('message_text',result.text)

    def test_status_poll_does_not_scan_source_messages(self):
        statements=[]
        original=repo.connect
        def traced(path=None):
            conn=original(path);conn.set_trace_callback(statements.append);return conn
        with patch.object(repo,'connect',side_effect=traced):repo.snapshot()
        self.assertFalse(any('messages_reactions' in s for s in statements))

    def test_partial_long_message_never_counts_as_indexed(self):
        self.execute("UPDATE messages_reactions SET message_text=? WHERE chat_id=-42 AND message_id=1",('Большой текст сообщения. '*1200,))
        repo.reconcile();generation=repo.plan_day(-42,'2026-09-01')
        self.execute("UPDATE ai_rag_chunks SET state='uploaded' WHERE id=(SELECT id FROM ai_rag_chunks WHERE generation=? LIMIT 1)",(generation,))
        self.assertFalse(repo.activate_day(-42,'2026-09-01',generation))
        self.assertEqual(repo.snapshot()['indexed_messages'],0)
        self.execute("UPDATE ai_rag_chunks SET state='uploaded' WHERE generation=?",(generation,))
        self.assertTrue(repo.activate_day(-42,'2026-09-01',generation))
        self.assertEqual(repo.snapshot()['indexed_messages'],2)

    def test_finished_initial_run_does_not_restart_daytime_after_edits(self):
        self.ready();self.ready(-43)
        self.execute("UPDATE ai_rag_runs SET finished_at=?,state='completed' WHERE kind='initial'",(repo.stamp(),))
        self.execute("UPDATE messages_reactions SET message_text='Новая версия' WHERE chat_id=-42 AND message_id=1")
        repo.reconcile()
        with closing(repo.connect()) as conn:self.assertFalse(service.initial_pending(conn))

    def test_new_messages_during_upload_do_not_restart_build_or_inflate_goal(self):
        generation=repo.plan_day(-42,'2026-09-01')
        self.execute("INSERT INTO messages_reactions VALUES(-42,3,1,'2026-09-01T10:02:00','Новая реплика',0)")
        repo.reconcile()
        with closing(repo.connect()) as conn:
            day=conn.execute('SELECT * FROM ai_rag_days WHERE chat_id=-42').fetchone()
            self.assertEqual(day['status'],'uploading');self.assertEqual(day['generation'],generation)
        self.execute("UPDATE ai_rag_chunks SET state='uploaded' WHERE generation=?",(generation,))
        self.assertTrue(repo.activate_day(-42,'2026-09-01',generation))
        self.assertEqual(repo.snapshot()['indexed_messages'],2)
        with closing(repo.connect()) as conn:
            self.assertEqual(conn.execute('SELECT indexed FROM ai_rag_message_status WHERE chat_id=-42 AND message_id=3').fetchone()[0],0)

    def test_fresh_updates_cannot_starve_unfinished_initial_history(self):
        self.ready()
        self.execute("INSERT INTO messages_reactions VALUES(-42,3,1,'2026-10-02T10:02:00','Свежая переписка',0)")
        repo.reconcile()
        with closing(repo.connect()) as conn,conn:
            repo.set_state(conn,'index_steps','3')
        self.assertEqual(service.pick_day()['chat_id'],-43)

    def test_seed_snapshot_resumes_in_bounded_batches(self):
        self.execute('DELETE FROM ai_rag_state')
        self.execute('DELETE FROM ai_rag_message_status')
        with patch.object(repo,'BOOTSTRAP_BATCH',2):
            self.assertFalse(repo.bootstrap())
            self.assertEqual(repo.snapshot()['seeded_messages'],2)
            self.assertTrue(repo.bootstrap())
        self.assertEqual(repo.snapshot()['eligible_messages'],3)

    def test_enabled_chat_still_excludes_commands_and_empty_messages(self):
        self.execute("INSERT INTO settings(chat_id,name,value) VALUES(-42,'ai_source','off')")
        repo.reconcile()
        self.execute("INSERT INTO messages_reactions VALUES(-42,3,1,'2026-09-01T10:02:00','/summary',0)")
        self.execute("INSERT INTO messages_reactions VALUES(-42,4,1,'2026-09-01T10:03:00','',0)")
        repo.reconcile()
        self.execute("UPDATE settings SET value='local' WHERE chat_id=-42 AND name='ai_source'")
        repo.reconcile()
        with closing(repo.connect()) as conn:
            rows=dict(conn.execute('SELECT message_id,eligible FROM ai_rag_message_status WHERE chat_id=-42'))
        self.assertEqual(rows,{1:1,2:1,3:0,4:0})
