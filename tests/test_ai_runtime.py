import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import ai_tasks
import ai_runtime as rt


class RuntimeTests(unittest.TestCase):
    def test_unavailable_classification_and_search_without_payload(self):
        rt.set_mode(-42,'api')
        for queue in ('type-checks','search-plans'):
            self.task(queue=queue)
            task=rt.claim(queue,'vps')
            with self.api() as (client,server,send):
                result=self.post_result(client,task,'',error='minute quota',error_kind='unavailable',metadata={'refusal_kind':'minute','provider_cooldown':False,'retry_after':60})
                self.assertEqual(result.json()['status'],'waiting')
                send.assert_not_called()

    def test_failure_before_delivery_does_not_strand_receipt(self):
        rt.set_mode(-42,'api');self.task(queue='type-checks');task=rt.claim('type-checks','vps')
        with self.api() as (client,server,send):
            with patch.object(rt,'defer',side_effect=sqlite3.OperationalError('temporary lock')):
                with self.assertRaises(sqlite3.OperationalError):
                    self.post_result(client,task,'',error='quota',error_kind='unavailable')
            with self.connection() as conn:
                self.assertIsNone(conn.execute('SELECT 1 FROM ai_receipts WHERE token=?',(task['lease_token'],)).fetchone())
            result=self.post_result(client,task,'',error='quota',error_kind='unavailable',metadata={'refusal_kind':'minute','provider_cooldown':False})
            self.assertEqual(result.json()['status'],'waiting');send.assert_not_called()

    def test_combined_worker_claim_prioritizes_direct_classification(self):
        self.task(kind='chat_summary')
        self.task(queue='type-checks')
        with self.api() as (client,server,send):
            response=client.get('/api/ai/workers/next',params={'worker_id':'pc','queues':'tasks,type-checks','wait_seconds':0},headers={'Authorization':'Bearer test-secret'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['task']['queue'],'type-checks')

    def test_background_profile_waits_for_night_and_rag(self):
        self.task(kind='profile_update')
        with self.connection() as conn:conn.execute("UPDATE ai_tasks SET payload_json=?",(json.dumps({'background':True}),))
        with patch.object(rt,'now',return_value=datetime(2026,10,5,2,0)):
            rt.heartbeat('pc','local',list(rt.TABLES))
            self.assertIsNone(rt.claim('tasks','pc'))
        with patch.object(rt,'now',return_value=datetime(2026,10,4,23,0)):
            rt.heartbeat('pc','local',list(rt.TABLES))
            with self.connection() as conn:conn.execute("INSERT OR REPLACE INTO ai_rag_state VALUES('night_open','1')")
            self.assertIsNone(rt.claim('tasks','pc'))
            with self.connection() as conn:conn.execute("UPDATE ai_rag_state SET value='0' WHERE key='night_open'")
            self.assertIsNotNone(rt.claim('tasks','pc'))

    def test_three_direct_requests_are_independent(self):
        for mid in (101,102,103):
            self.assertIsNotNone(ai_tasks.create_type_check_task(chat_id=-42,user_id=1,request_message_id=mid,message_text='Бот, привет',trigger_reason='mention'))
        self.assertIsNone(ai_tasks.create_type_check_task(chat_id=-42,user_id=1,request_message_id=104,message_text='Бот, привет',trigger_reason='mention'))
        self.assertIsNotNone(ai_tasks.create_type_check_task(chat_id=-42,user_id=2,request_message_id=105,message_text='Бот, привет',trigger_reason='mention'))

    def test_refusal_kinds_distinguish_minute_and_daily(self):
        from ai_providers import refusal_kind
        from unittest.mock import Mock
        response=Mock(status_code=429)
        self.assertEqual(refusal_kind(response,{}),'minute')
        body={'error':{'details':[{'violations':[{'quotaId':'requestsPerDay'}]}]}}
        self.assertEqual(refusal_kind(response,body),'daily')

    def test_foreground_daily_quota_finishes_once(self):
        self.task();rt.set_mode(-42,'api')
        task=rt.claim('tasks','vps')
        with self.api() as (client,server,send):
            result=self.post_result(client,task,'',error='quota',error_kind='unavailable',metadata={'refusal_kind':'daily','provider_cooldown':False})
            self.assertEqual(result.json()['status'],'failed')
            self.post_result(client,task,'',error='quota',error_kind='unavailable',metadata={'refusal_kind':'daily','provider_cooldown':False})
            self.assertEqual(send.call_count,1)

    def test_minute_retry_survives_restart_and_stops_after_one(self):
        self.task();rt.set_mode(-42,'api')
        task=rt.claim('tasks','vps')
        self.assertTrue(rt.defer('tasks',task['id'],'vps',task['lease_token'],'minute',60,False,'minute'))
        rt.initialize()
        with self.connection() as conn:conn.execute('UPDATE ai_tasks SET retry_at=?',(rt.stamp(-1),))
        task=rt.claim('tasks','vps')
        self.assertFalse(rt.defer('tasks',task['id'],'vps',task['lease_token'],'minute',60,False,'minute'))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.db'
        self.db_patch = patch.object(ai_tasks, 'DB_FILE', self.path)
        self.db_patch.start()
        rt.initialize()
        rt.heartbeat('pc', 'local', list(rt.TABLES))
        rt.heartbeat('vps', 'groq', list(rt.TABLES))

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def task(self, chat=-42, queue='tasks', kind='response'):
        with self.connection() as conn:
            table = rt.TABLES[queue]
            fields = 'status,model,prompt,chat_id,user_id,request_message_id,created_at,updated_at'
            message_id = conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0] + 101
            values = ['pending', 'gemma4:e4b', 'Synthetic prompt', chat, 1, message_id, rt.stamp(), rt.stamp()]
            if queue == 'tasks':
                fields += ',task_type';values += [kind]
            else:
                fields += ',message_text,trigger_reason';values += ['Synthetic', 'mention']
            return conn.execute(f"INSERT INTO {table}({fields}) VALUES ({','.join('?' for _ in values)})", values).lastrowid

    def test_old_database_migrates_twice_without_losing_tasks(self):
        ident = self.task()
        rt.initialize();rt.initialize()
        self.assertEqual(rt.mode(-42), 'local')
        self.assertEqual(rt.claim('tasks', 'pc')['id'], ident)
        self.assertIsNone(rt.claim('tasks', 'vps'))

    def test_heartbeat_does_not_repeat_schema_migration(self):
        with patch.object(rt, 'ensure_schema', side_effect=AssertionError('DDL in heartbeat')):
            rt.heartbeat('vps', 'groq', list(rt.TABLES))
            rt.heartbeat('vps', 'groq', list(rt.TABLES))

    def test_failed_schema_initialization_is_retryable(self):
        from schema_once import forget
        forget(self.path)
        with patch.object(rt, 'ensure_schema', side_effect=sqlite3.OperationalError('busy')):
            with self.assertRaises(sqlite3.OperationalError):
                rt.initialize(force=False)
        rt.initialize(force=False)
        rt.heartbeat('vps', 'groq', list(rt.TABLES))

    def test_every_mode_and_default_for_each_queue(self):
        for queue in rt.TABLES:
            for mode, owner in [('local','pc'),('api','vps'),('local_api','pc'),('api_local','vps'),('off',None)]:
                with self.subTest(queue=queue, mode=mode):
                    rt.set_mode(-42, 'off')
                    rt.set_mode(-42, mode)
                    self.task(queue=queue)
                    other = 'pc' if owner == 'vps' else 'vps'
                    self.assertIsNone(rt.claim(queue, other))
                    if owner:
                        self.assertIsNotNone(rt.claim(queue, owner))

    def test_transport_failure_switches_without_spending_format_attempt(self):
        rt.set_mode(-42, 'local_api');self.task()
        first = rt.claim('tasks', 'pc')
        rt.defer('tasks', first['id'], 'pc', first['lease_token'], 'timeout')
        second = rt.claim('tasks', 'vps')
        self.assertEqual(second['attempt'], 0)
        self.assertEqual(second['transport_attempt'], 1)
        self.assertFalse(rt.renew('tasks', first['id'], 'pc', first['lease_token']))
        with self.assertRaises(ValueError):rt.accept_result('tasks', first['id'], 'pc', first['lease_token'])

    def test_api_failure_switches_to_local(self):
        rt.set_mode(-42, 'api_local');self.task()
        first=rt.claim('tasks','vps');rt.defer('tasks',first['id'],'vps',first['lease_token'],'429')
        self.assertIsNotNone(rt.claim('tasks','pc'))

    def test_missing_pc_allows_external_reserve(self):
        rt.set_mode(-42,'local_api');self.task()
        with self.connection() as conn:conn.execute("UPDATE ai_workers SET heartbeat='2000-01-01' WHERE worker_id='pc'")
        self.assertIsNotNone(rt.claim('tasks','vps'))

    def test_strict_mode_waits_and_other_chat_can_run(self):
        self.task();rt.set_mode(-43,'api');self.task(-43)
        external=rt.claim('tasks','vps')
        self.assertEqual(external['chat_id'],-43)
        self.assertEqual(ai_tasks.get_task(1)['status'],'pending')

    def test_receipt_fences_concurrent_and_duplicate_results(self):
        self.task();task=rt.claim('tasks','pc');args=('tasks',task['id'],'pc',task['lease_token'])
        self.assertIsNone(rt.accept_result(*args))
        self.assertEqual(rt.accept_result(*args)['status'],'accepted')
        result={'ok':True,'status':'done'};rt.finish_receipt(task['lease_token'],result)
        self.assertEqual(rt.accept_result(*args),result)

    def test_off_cancels_old_and_prevents_new_tasks(self):
        self.task();task=rt.claim('tasks','pc');rt.set_mode(-42,'off')
        self.assertFalse(rt.renew('tasks',task['id'],'pc',task['lease_token']))
        self.assertEqual(ai_tasks.get_task(task['id'])['status'],'cancelled')
        self.assertIsNone(ai_tasks.create_type_check_task(chat_id=-42,user_id=1,request_message_id=1,message_text='test',trigger_reason='reply'))
        self.assertIsNone(ai_tasks.create_text_to_sql_task(chat_id=-42,user_id=1,request_message_id=1,user_query='test'))
        rt.set_mode(-42,'local');self.assertIsNone(rt.claim('tasks','pc'))

    def test_change_mode_revokes_only_disallowed_owner(self):
        self.task();first=rt.claim('tasks','pc');rt.set_mode(-42,'local_api')
        self.assertTrue(rt.renew('tasks',first['id'],'pc',first['lease_token']))
        rt.set_mode(-42,'api');second=rt.claim('tasks','vps')
        self.assertNotEqual(first['lease_token'],second['lease_token'])

    def test_expired_lease_can_be_reclaimed_after_restart(self):
        self.task();first=rt.claim('tasks','pc')
        with self.connection() as conn:conn.execute("UPDATE ai_tasks SET lease_until='2000-01-01'")
        second=rt.claim('tasks','pc');self.assertNotEqual(first['lease_token'],second['lease_token'])

    def test_model_budgets_are_separate_and_reservations_survive_restart(self):
        with patch.dict(os.environ,{'GROQ_TPM':'8000','GROQ_TPD':'10000'}):
            reservation=rt.reserve(rt.MODEL_LIGHT,6000,1500)
            self.assertIsNotNone(reservation)
            self.assertIsNone(rt.reserve(rt.MODEL_LIGHT,1000,1000))
            self.assertIsNotNone(rt.reserve(rt.MODEL_HEAVY,1000,1000))
            rt.settle(reservation,{'prompt_tokens':100,'completion_tokens':20,'prompt_tokens_details':{'cached_tokens':80}})
            self.assertIsNotNone(rt.reserve(rt.MODEL_LIGHT,1000,1000))

    def test_deferred_task_does_not_hold_lease_and_waits_without_failure(self):
        self.task();task=rt.claim('tasks','pc');rt.defer('tasks',task['id'],'pc',task['lease_token'],'unavailable')
        self.assertIsNone(rt.claim('tasks','pc'))
        row=ai_tasks.get_task(task['id']);self.assertEqual(row['status'],'pending');self.assertIsNone(row['lease_token'])

    @contextmanager
    def api(self):
        from fastapi.testclient import TestClient
        from web import server
        with patch.object(server,'AI_WORKER_TOKEN','test-secret'), patch.object(server,'_send_telegram_message',return_value=123) as send, patch.object(server,'_set_telegram_reaction'), patch.object(server,'_get_ai_task_user_context',return_value=('Synthetic',None)):
            yield TestClient(server.app), server, send

    def seed_messages(self):
        with self.connection() as conn:
            conn.execute('CREATE TABLE users (user_id INTEGER, chat_id INTEGER, name TEXT,nick TEXT)')
            conn.execute('CREATE TABLE messages_reactions (chat_id INTEGER,message_id INTEGER,user_id INTEGER,message_text TEXT,date TEXT,reactions_count INTEGER)')
            conn.executemany('INSERT INTO messages_reactions VALUES (?,?,?,?,?,0)',[(-42,1,1,'Synthetic message one',rt.stamp()),(-42,2,1,'Synthetic message two',rt.stamp()),(-99,3,1,'Other chat',rt.stamp())])

    def post_result(self, client, task, output, **extras):
        return client.post(f"/api/ai/{task['queue']}/{task['id']}/result", headers={'Authorization':'Bearer test-secret'},json={'worker_id':task['worker_id'],'lease_token':task['lease_token'],'output':output,**extras})

    def test_api_claim_requires_registered_worker_and_old_results_rejected(self):
        self.task()
        with self.api() as (client,server,send):
            self.assertEqual(client.get('/api/ai/tasks/next',headers={'Authorization':'Bearer test-secret'}).status_code,409)
            result=client.get('/api/ai/tasks/next?worker_id=pc',headers={'Authorization':'Bearer test-secret'})
            self.assertEqual(result.status_code,200)
            self.assertEqual(client.post('/api/ai/tasks/1/result',headers={'Authorization':'Bearer test-secret'},json={'output':'test'}).status_code,422)

    def test_response_delivered_once_and_old_or_cancelled_results_rejected(self):
        self.task();task=rt.claim('tasks','pc')
        with self.api() as (client,server,send):
            response=self.post_result(client,task,'Тестовый ответ.')
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['status'],'done')
            self.assertEqual(self.post_result(client,task,'Тестовый ответ.').json()['status'],'done')
            self.assertEqual(send.call_count,1)
            self.task(-43);task=rt.claim('tasks','pc');rt.set_mode(-43,'off')
            self.assertEqual(self.post_result(client,task,'Поздний ответ.').status_code,409)
            self.assertEqual(send.call_count,1)

    def test_typing_sql_analysis_chain_uses_shared_validators(self):
        self.seed_messages()
        ident=ai_tasks.create_type_check_task(chat_id=-42,user_id=1,request_message_id=10,message_text='Проанализируй активность',trigger_reason='mention')
        task=rt.claim('type-checks','pc')
        with self.api() as (client,server,send):
            result=self.post_result(client,task,'data_analysis')
            self.assertEqual(result.status_code,200,result.text)
            sqltask=rt.claim('tasks','pc');self.assertEqual(sqltask['task_type'],'data_analysis_sql')
            result=self.post_result(client,sqltask,'SELECT COUNT(*) AS total FROM messages_reactions WHERE chat_id = -42;')
            self.assertEqual(result.json()['status'],'done',result.text)
            analysis=rt.claim('tasks','pc');self.assertEqual(analysis['task_type'],'data_analysis_response')
            result=self.post_result(client,analysis,'В чате два сообщения. Данных мало для уверенных выводов.')
            self.assertEqual(result.json()['status'],'done',result.text)
            self.assertEqual(send.call_count,1)

    def test_search_chain_keeps_searxng_and_no_duplicate_child(self):
        self.seed_messages()
        ai_tasks.create_type_check_task(chat_id=-42,user_id=1,request_message_id=10,message_text='Какая погода?',trigger_reason='mention')
        task=rt.claim('type-checks','pc')
        with self.api() as (client,server,send), patch.object(server,'build_web_context',return_value='Synthetic search result') as search:
            result=self.post_result(client,task,'web_search');self.assertEqual(result.json()['status'],'done',result.text)
            searchtask=rt.claim('search-plans','pc')
            plan=json.dumps({'queries':['test'],'needed_facts':['weather'],'answer_strategy':'short'})
            result=self.post_result(client,searchtask,plan)
            self.assertEqual(result.json()['status'],'done',result.text)
            self.post_result(client,searchtask,plan);self.assertEqual(search.call_count,1)
            response=rt.claim('tasks','pc');self.assertIn('Synthetic search result',response['prompt'])
            self.assertEqual(self.post_result(client,response,'Тестовый ответ с результатом поиска.').json()['status'],'done')

    def test_bad_type_output_fails_and_bad_sql_cannot_write(self):
        self.seed_messages()
        ai_tasks.create_type_check_task(chat_id=-42,user_id=1,request_message_id=10,message_text='test',trigger_reason='mention')
        with self.api() as (client,server,send):
            result=self.post_result(client,rt.claim('type-checks','pc'),'not_a_type')
            self.assertEqual(result.json()['status'],'failed')
            ai_tasks.create_text_to_sql_task(chat_id=-42,user_id=1,request_message_id=11,user_query='test')
            result=self.post_result(client,rt.claim('tasks','pc'),'DELETE FROM messages_reactions;')
            self.assertEqual(result.json()['status'],'retry')
            with self.connection() as conn:self.assertEqual(conn.execute('SELECT count(*) FROM messages_reactions').fetchone()[0],3)
            send.assert_not_called()

    def test_sql_retry_new_lease_and_old_result_is_idempotent(self):
        self.seed_messages()
        ai_tasks.create_text_to_sql_task(chat_id=-42,user_id=1,request_message_id=77,user_query='Сколько участников?')
        first=rt.claim('tasks','pc')
        with self.api() as (client,server,send):
            retry=self.post_result(client,first,'SELECT missing_column FROM users WHERE chat_id=-42').json()
            self.assertEqual(retry['status'],'retry')
            second=rt.claim('tasks','pc')
            self.assertIsNotNone(second)
            self.assertEqual(second['id'],first['id'])
            self.assertNotEqual(second['lease_token'],first['lease_token'])
            self.assertEqual(second['attempt'],1)
            self.assertEqual(self.post_result(client,first,'SELECT bad').json(),retry)
            result=self.post_result(client,second,'SELECT name FROM users WHERE chat_id=-42').json()
            self.assertEqual(result['status'],'done')
            self.post_result(client,second,'SELECT name FROM users WHERE chat_id=-42')
            self.assertEqual(send.call_count,1)

    def test_unfinished_receipt_does_not_allow_pending_replay(self):
        self.task();task=rt.claim('tasks','pc')
        rt.accept_result('tasks',task['id'],'pc',task['lease_token'])
        with self.connection() as conn:
            conn.execute("UPDATE ai_tasks SET status='pending',lease_until=NULL WHERE id=?",(task['id'],))
        self.assertIsNone(rt.claim('tasks','pc'))
        rt.finish_receipt(task['lease_token'],{'ok':False,'status':'delivery_unknown','task_id':task['id']})
        self.assertIsNone(rt.claim('tasks','pc'))

    def test_consecutive_sql_classifications_are_not_throttled(self):
        self.seed_messages()
        with self.api() as (client,server,send):
            ids=[]
            for ident in (80,81,82):
                ai_tasks.create_type_check_task(chat_id=-42,user_id=1,request_message_id=ident,message_text='Бот, покажи статистику',trigger_reason='mention')
                task=rt.claim('type-checks','pc')
                result=self.post_result(client,task,'text_to_sql').json()
                self.assertIsNone(result['skipped_reason'])
                ids.append(result['final_task_id'])
            self.assertEqual(len(set(ids)),3)
            self.assertNotIn(None,ids)

    def test_bot_explicit_request_bypasses_busy_chat_and_response_cooldown(self):
        # Execute the actual handler without importing main.py (which creates Bot).
        import ast,asyncio,types,logging
        tree=ast.parse((Path(__file__).resolve().parents[1]/'main.py').read_text(encoding='utf-8-sig'))
        function=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='maybe_create_ai_response_task')
        function.decorator_list=[]
        from unittest.mock import Mock
        blocked=Mock(return_value=True);cooldown=Mock(return_value=60)
        scope={'types':types.SimpleNamespace(Message=object),'asyncio':asyncio,'logging':logging,
               'ai_enabled':lambda chat:True,'_get_ai_response_trigger':lambda message:'mention_bot_word',
               'has_pending_response_task':blocked,'has_pending_type_check':blocked,'get_response_cooldown_left':cooldown,
               'create_type_check_task':ai_tasks.create_type_check_task}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'main.py','exec'),scope)
        message=types.SimpleNamespace(chat=types.SimpleNamespace(id=-42),from_user=types.SimpleNamespace(id=1,is_bot=False),text='Бот, покажи баланс',message_id=88)
        asyncio.run(scope['maybe_create_ai_response_task'](message))
        self.assertTrue(ai_tasks.has_pending_type_check(chat_id=-42,request_message_id=88))
        blocked.assert_not_called();cooldown.assert_not_called()

    def test_db_command_accepts_two_immediate_requests(self):
        import ast,asyncio,types,logging
        from unittest.mock import Mock,AsyncMock
        tree=ast.parse((Path(__file__).resolve().parents[1]/'main.py').read_text(encoding='utf-8-sig'))
        function=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='db_text_to_sql_command')
        function.decorator_list=[]
        scope={'types':types.SimpleNamespace(Message=object),'CommandObject':object,'logging':logging,
               'ai_enabled':lambda chat:True,'add_or_update_user':Mock(),'create_text_to_sql_task':ai_tasks.create_text_to_sql_task}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'main.py','exec'),scope)
        for ident in (89,90):
            message=types.SimpleNamespace(chat=types.SimpleNamespace(id=-42),from_user=types.SimpleNamespace(id=1,full_name='Synthetic',username=None),message_id=ident,reply=AsyncMock())
            asyncio.run(scope['db_text_to_sql_command'](message,types.SimpleNamespace(args='Покажи баланс')))
            self.assertIn('в очереди',message.reply.call_args.args[0])
        with self.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM ai_tasks WHERE task_type='text_to_sql'").fetchone()[0],2)

    def test_model_fallback_on_429_and_no_secret_in_exception(self):
        from ai_providers import call_external
        from unittest.mock import Mock
        limited=Mock(ok=False,status_code=429,headers={'retry-after':'60'})
        counted=Mock(ok=True);counted.json.return_value={'totalTokens':10}
        success=Mock(ok=True);success.json.return_value={'candidates':[{'content':{'parts':[{'text':'answer'}]},'finishReason':'STOP'}]}
        with patch.dict(os.environ,{'GROQ_API_KEY':'fake-key','GEMINI_API_KEY':'fake-key'}),patch('ai_http.post',side_effect=[limited,counted,success]) as post:
            output,meta=call_external({'prompt':'Synthetic','task_type':'text_to_sql'},45)
        self.assertEqual(output,'answer');self.assertEqual(meta['model'],rt.MODEL_GOOGLE_PRIMARY)
        self.assertFalse(rt.model_ready(rt.MODEL_HEAVY));self.assertEqual(post.call_count,3)

    def test_external_chains_include_background_work(self):
        from ai_providers import external_models
        google=(rt.MODEL_GOOGLE_PRIMARY,rt.MODEL_GOOGLE_SECONDARY,rt.MODEL_GOOGLE_LAST)
        for kind in ('response','search_plan','type_check','chat_summary','profile_update'):
            self.assertEqual(external_models(kind),google)
        for kind in ('text_to_sql','data_analysis_sql','data_analysis_response'):
            self.assertEqual(external_models(kind),(rt.MODEL_HEAVY,)+google)

    def test_google_thought_filter_and_input_only_quota(self):
        from ai_providers import call_google
        from unittest.mock import Mock
        counted=Mock(ok=True);counted.json.return_value={'totalTokens':12}
        success=Mock(ok=True);success.json.return_value={
            'candidates':[{'finishReason':'STOP','content':{'parts':[{'thought':True,'text':'private reasoning'},{'text':'final answer'}]}}],
            'usageMetadata':{'promptTokenCount':12,'candidatesTokenCount':5,'thoughtsTokenCount':7}}
        with patch.dict(os.environ,{'GEMINI_API_KEY':'fake'}),patch('ai_http.post',side_effect=[counted,success]):
            output,meta=call_google({'prompt':'Synthetic','task_type':'response'},45,rt.MODEL_GOOGLE_LAST)
        self.assertEqual(output,'final answer');self.assertEqual(meta['usage']['completion_tokens'],12)
        self.assertIsNotNone(rt.reserve(rt.MODEL_GOOGLE_LAST,15900,100000))
        self.assertGreater(rt.budget_wait(rt.MODEL_GOOGLE_LAST,200),0)
        self.assertEqual(rt.budget_wait(rt.MODEL_GOOGLE_LAST,1),0)

    def test_google_minute_reset_and_separate_models(self):
        from datetime import datetime,timedelta
        moment=datetime(2026,10,2,12)
        with patch.object(rt,'now',return_value=moment):
            self.assertIsNotNone(rt.reserve(rt.MODEL_GOOGLE_LAST,15000,100))
            self.assertIsNone(rt.reserve(rt.MODEL_GOOGLE_LAST,2000,0))
            self.assertIsNotNone(rt.reserve(rt.MODEL_GOOGLE_PRIMARY,2000,0))
        with patch.object(rt,'now',return_value=moment+timedelta(seconds=61)):
            self.assertIsNotNone(rt.reserve(rt.MODEL_GOOGLE_LAST,15000,0))

    def test_google_day_reset_keeps_previous_minute_budget(self):
        from datetime import datetime,timedelta
        before=datetime(2026,10,2,6,59,50) # Midnight PDT at 07:00 UTC.
        with patch.dict(os.environ,{'GEMINI_RPD':'1'}),patch.object(rt,'now',return_value=before):
            rt.reserve(rt.MODEL_GOOGLE_PRIMARY,249000,0)
            self.assertEqual(rt.budget_wait(rt.MODEL_GOOGLE_PRIMARY),11)
        with patch.dict(os.environ,{'GEMINI_RPD':'1'}),patch.object(rt,'now',return_value=before+timedelta(seconds=20)):
            self.assertGreater(rt.budget_wait(rt.MODEL_GOOGLE_PRIMARY,2000),0)
            self.assertEqual(rt.budget_wait(rt.MODEL_GOOGLE_PRIMARY,1),0)

    def test_waiting_large_task_does_not_block_small_task(self):
        rt.set_mode(-42,'api');self.task();large=rt.claim('tasks','vps')
        rt.defer('tasks',large['id'],'vps',large['lease_token'],'budget',retry_seconds=61,provider_cooldown=False)
        self.task();small=rt.claim('tasks','vps')
        self.assertIsNotNone(small);self.assertNotEqual(small['id'],large['id'])
        self.assertEqual(ai_tasks.get_task(large['id'])['attempt'],0)

    def test_google_truncation_is_not_published(self):
        from ai_providers import call_google,ProviderUnavailable
        from unittest.mock import Mock
        counted=Mock(ok=True);counted.json.return_value={'totalTokens':10}
        incomplete=Mock(ok=True);incomplete.json.return_value={'candidates':[{'finishReason':'MAX_TOKENS','content':{'parts':[{'text':'partial'}]}}]}
        with patch.dict(os.environ,{'GEMINI_API_KEY':'fake'}),patch('ai_http.post',side_effect=[counted,incomplete]):
            with self.assertRaises(ProviderUnavailable):call_google({'prompt':'Synthetic','task_type':'response'},45,rt.MODEL_GOOGLE_PRIMARY)
        self.assertFalse(rt.model_ready(rt.MODEL_GOOGLE_PRIMARY))

    def test_google_429_retry_info_and_secret_redaction(self):
        from ai_providers import call_google,ProviderUnavailable
        from unittest.mock import Mock
        limited=Mock(ok=False,status_code=429,headers={})
        limited.json.return_value={'error':{'message':'fake-key secret prompt','details':[{'retryDelay':'61.2s'}]}}
        with patch.dict(os.environ,{'GEMINI_API_KEY':'fake-key'}),patch('ai_http.post',return_value=limited):
            with self.assertRaises(ProviderUnavailable) as result:call_google({'prompt':'Synthetic','task_type':'response'},45,rt.MODEL_GOOGLE_PRIMARY)
        self.assertEqual(result.exception.retry_after,62)
        self.assertNotIn('fake-key',str(result.exception));self.assertFalse(rt.model_ready(rt.MODEL_GOOGLE_PRIMARY))

    def test_both_sources_unavailable_do_not_spin(self):
        rt.set_mode(-42,'local_api');self.task();first=rt.claim('tasks','pc')
        rt.defer('tasks',first['id'],'pc',first['lease_token'],'timeout')
        second=rt.claim('tasks','vps')
        rt.defer('tasks',second['id'],'vps',second['lease_token'],'all budgets exhausted',retry_seconds=3600,provider_cooldown=False)
        self.assertIsNone(rt.claim('tasks','vps'));self.assertIsNone(rt.claim('tasks','pc'))
        self.assertGreater(ai_tasks.get_task(first['id'])['retry_at'],rt.stamp())

    def test_compact_sql_prompt_and_context_budget(self):
        from ai_providers import prepare_prompt,tokenizer,PromptTooLarge
        prompt=ai_tasks.build_text_to_sql_prompt('Сколько сообщений?',-42)
        compact=prepare_prompt({'prompt':prompt,'task_type':'text_to_sql'})
        self.assertLessEqual(len(tokenizer().encode(compact)),6000)
        self.assertIn('messages_reactions',compact);self.assertIn('chat_id',compact)
        oversized='Mandatory '*10000
        with self.assertRaises(PromptTooLarge):prepare_prompt({'prompt':oversized,'task_type':'type_check'})

    def test_telegram_delivery_failure_does_not_regenerate(self):
        self.task();task=rt.claim('tasks','pc')
        with self.api() as (client,server,send):
            send.side_effect=rt.DeliveryError('unknown')
            result=self.post_result(client,task,'Ответ')
            self.assertEqual(result.json()['status'],'delivery_unknown',result.text)
            self.assertEqual(ai_tasks.get_task(task['id'])['status'],'failed')
            self.assertEqual(ai_tasks.get_task(task['id'])['attempt'],0)
            self.post_result(client,task,'Ответ');self.assertEqual(send.call_count,1)

    def test_partial_grounded_series_is_not_delivered_again(self):
        import ai_grounding as grounding
        rt.set_mode(-42,'api')
        grounding.create_task({'chat_id':-42,'user_id':123,'request_message_id':99},'web_grounding',{},'Question')
        task=rt.claim('tasks','vps')
        result={'status':'grounded','tool':'search','text':'**'+('Подробный ответ 🗺️. '*700)+'** [1]','sources':[{'title':'Synthetic','url':'https://example.test/news'}]}
        with self.api() as (client,server,send):
            send.side_effect=[123,rt.DeliveryError('unknown after first message')]
            response=self.post_result(client,task,json.dumps(result))
            self.assertEqual(response.json()['status'],'delivery_unknown',response.text)
            self.assertEqual(ai_tasks.get_task(task['id'])['status'],'failed')
            self.post_result(client,task,json.dumps(result));self.assertEqual(send.call_count,2)
            self.assertIsNone(rt.claim('tasks','vps'))

    def test_large_analysis_context_keeps_complete_json_rows(self):
        analysis={'chat_id':-42,'request_message_id':1,'sql_text':'SELECT 1',
                  'columns_json':'["text"]','rows_json':json.dumps([{'text':'x'*500}]*500),
                  'preview_text':'Synthetic','truncated':0,'user_query':'Explain'}
        with patch.object(ai_tasks,'get_response_short_memory',return_value=[]),patch.object(ai_tasks,'get_response_long_memory',return_value=[]):
            prompt=ai_tasks.build_data_analysis_response_prompt(analysis=analysis)
        block=prompt.split('SQL-result JSON.',1)[1].split('\n',1)[1].split("Результат был усечён backend'ом:",1)[0].strip()
        rows=json.loads(block)
        self.assertTrue(rows);self.assertLess(len(rows),500)
        self.assertIn("Результат был усечён backend'ом: да",prompt)

    def test_ai_source_menu_marks_choice_and_keeps_exact_labels(self):
        import settings
        keyboard=settings.get_ai_source_keyboard(-42)
        self.assertEqual([r[0].text.lstrip('✓ ') for r in keyboard.inline_keyboard],list(rt.MODES.values())+['Назад'])
        self.assertEqual(keyboard.inline_keyboard[1][0].text,'✓ Только локальная модель')
        rt.set_mode(-42,'api')
        self.assertEqual(settings.get_ai_source_keyboard(-42).inline_keyboard[0][0].text,'✓ Только внешний API')

    def test_maps_full_api_chain_and_duplicate_delivery(self):
        rt.set_mode(-42,'api')
        ai_tasks.create_type_check_task(chat_id=-42,user_id=123,request_message_id=99,message_text='Найди музей в Париже',trigger_reason='mention')
        classifier=rt.claim('type-checks','vps')
        plan={'queries':['Find museums in Paris'],'needed_facts':['address'],'answer_strategy':'answer','location':'Париже','needs_clarification':False,'clarification':''}
        with self.api() as (client,server,send):
            first=self.post_result(client,classifier,'maps');self.assertEqual(first.json()['result_type'],'maps',first.text)
            planner=rt.claim('search-plans','vps');self.assertIn('needs_clarification',planner['prompt'])
            second=self.post_result(client,planner,json.dumps(plan));self.assertIsNotNone(second.json()['final_task_id'],second.text)
            grounded=rt.claim('tasks','vps');self.assertEqual(grounded['task_type'],'maps_grounding')
            result={'status':'grounded','tool':'maps','text':'A museum [1].','sources':[{'title':'Museum','url':'https://maps.google.com/maps?cid=123'}]}
            third=self.post_result(client,grounded,json.dumps(result));self.assertEqual(third.json()['status'],'done',third.text);send.assert_not_called()
            self.post_result(client,grounded,json.dumps(result))
            translation=rt.claim('tasks','vps');self.assertEqual(translation['task_type'],'maps_translation')
            translated='### Музей\n\n**'+('Описание музея 🗺️. '*600)+'** [1].'
            last=self.post_result(client,translation,translated);self.assertEqual(last.json()['status'],'done',last.text)
            count=send.call_count;self.assertGreater(count,1)
            self.post_result(client,translation,translated);self.assertEqual(send.call_count,count)
            self.assertIn('Google Maps',send.call_args.args[1])
            self.assertIn('<b>Музей</b>',send.call_args_list[0].args[1])
            self.assertEqual(send.call_args_list[0].kwargs['reply_to_message_id'],99)
            self.assertIsNone(send.call_args_list[1].kwargs['reply_to_message_id'])
            self.assertNotIn('[1]',''.join(call.args[1] for call in send.call_args_list))
            self.assertEqual(server._set_telegram_reaction.call_args.args[-1],server.RESPONSE_REACTION_DONE)

    def test_nearby_plan_creates_clarification_without_grounding(self):
        ai_tasks.create_search_plan_task(chat_id=-42,user_id=123,request_message_id=99,message_text='Кафе рядом',trigger_reason='maps')
        planner=rt.claim('search-plans','pc')
        plan={'queries':['cafes near me'],'needed_facts':['address'],'answer_strategy':'answer','location':'Екатеринбург','needs_clarification':False,'clarification':''}
        with self.api() as (client,server,send),patch.dict(os.environ,{'AI_CREATOR_USER_ID':'123'}):
            response=self.post_result(client,planner,json.dumps(plan));self.assertEqual(response.json()['status'],'done',response.text)
            notice=rt.claim('tasks','pc');self.assertEqual(notice['task_type'],'grounding_notice')
            from ai_providers import call_local
            output,_=call_local(notice,30)
            self.assertNotIn(ai_tasks.CREATOR_POLICY_MARKER,output)
            self.post_result(client,notice,ai_tasks.CREATOR_POLICY_MARKER+' private backend instruction')
            self.assertIn('Уточните',send.call_args.args[1]);self.assertNotIn(ai_tasks.CREATOR_POLICY_MARKER,send.call_args.args[1])

    def test_two_workers_cannot_claim_the_same_task(self):
        from concurrent.futures import ThreadPoolExecutor
        self.task();rt.heartbeat('pc2','local',['tasks'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda w:rt.claim('tasks',w),['pc','pc2']))
        self.assertEqual(sum(result is not None for result in results),1)

    def test_selected_source_click_and_unauthorized_callback(self):
        import asyncio
        import settings
        from aiogram import Dispatcher
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        dp=Dispatcher();settings.register_settings_handlers(dp)
        handler=next(h.callback for h in dp.callback_query.handlers if h.callback.__name__=='ai_source_menu')
        callback=SimpleNamespace(data='ai_source:local',from_user=SimpleNamespace(id=next(iter(settings.ADMIN_IDS))),
                                 message=SimpleNamespace(chat=SimpleNamespace(id=-42),edit_text=AsyncMock(),delete=AsyncMock(),answer=AsyncMock()),answer=AsyncMock())
        state=SimpleNamespace(clear=AsyncMock())
        asyncio.run(handler(callback,state))
        callback.answer.assert_awaited_once();callback.message.edit_text.assert_not_awaited()
        callback.message.delete.assert_awaited_once();callback.message.answer.assert_awaited_once()
        callback.from_user.id=-999;callback.data='ai_source:api';callback.answer.reset_mock()
        asyncio.run(handler(callback,state))
        self.assertEqual(rt.mode(-42),'local');callback.answer.assert_awaited_once_with('Нет прав для изменения настройки.',show_alert=True)

    def test_empty_russian_search_retries_without_language_filter(self):
        import io
        import web_search
        empty=io.BytesIO(b'{"results":[]}')
        found=io.BytesIO(json.dumps({'results':[{'url':'https://example.org','title':'Synthetic'}]}).encode())
        with patch.object(web_search,'_CACHE',{}),patch.object(web_search,'urlopen',side_effect=[empty,found]) as get:
            self.assertEqual(len(web_search.searxng_search('Synthetic')),1)
            self.assertIn('language=all',get.call_args.args[0].full_url)
            self.assertEqual(get.call_count,2)

    def test_original_database_columns_migrate_idempotently(self):
        with self.connection() as conn:
            conn.execute('DROP TABLE ai_tasks')
        ai_tasks.ensure_ai_tasks_table(force=True)
        self.task()
        rt.initialize();rt.initialize()
        self.assertIsNotNone(rt.claim('tasks','pc'))

    def test_summary_and_profile_results_use_existing_backend(self):
        self.task(kind='chat_summary');task=rt.claim('tasks','pc')
        with self.api() as (client,server,send):
            self.assertEqual(self.post_result(client,task,'Обсуждали синтетические данные.').json()['status'],'done')
            self.task(kind='profile_update')
            with self.connection() as conn:conn.execute("UPDATE ai_tasks SET payload_json=? WHERE task_type='profile_update'",(json.dumps({'background':False}),))
            task=rt.claim('tasks','pc')
            profile={key:[] for key in ai_tasks.PROFILE_ARRAY_LIMITS}
            profile.update(display_name='Synthetic',communication_style='Краткий',confidence='low',short_summary='Мало данных.')
            result=self.post_result(client,task,json.dumps(profile,ensure_ascii=False))
            self.assertEqual(result.json()['status'],'done',result.text)
            send.assert_not_called()


if __name__=='__main__':unittest.main()
