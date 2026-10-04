import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import ai_audit
import ai_tasks
import ai_runtime
from web import ai_dashboard as dashboard
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


class DashboardTests(unittest.TestCase):
    def test_aggregates_cached_but_rows_remain_fresh(self):
        ident=self.task()
        first=dashboard.list_tasks(self.path)
        self.execute("UPDATE ai_tasks SET result_text='fresh result' WHERE id=?",(ident,))
        with patch.object(dashboard,'union',wraps=dashboard.union) as union:
            second=dashboard.list_tasks(self.path)
            self.assertEqual(first['stats'],second['stats'])
            self.assertEqual(second['rows'][0]['response_preview'],'fresh result')
            self.assertEqual(union.call_count,1)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'test.db'
        self.patch = patch.object(ai_tasks,'DB_FILE',self.path)
        self.patch.start()
        ai_runtime.initialize()

    def tearDown(self):
        ai_audit.take()
        self.patch.stop()
        self.tmp.cleanup()

    def execute(self, query, args=()):
        with closing(sqlite3.connect(self.path)) as conn:
            with conn:
                return conn.execute(query,args).lastrowid

    def task(self, kind='response', chat=-12, user=123, queue='tasks', date=None):
        table = ai_runtime.TABLES[queue]
        fields='status,model,prompt,chat_id,user_id,request_message_id,created_at,updated_at'
        values=['done','planned-local','original context',chat,user,100+user, date or ai_runtime.stamp(),ai_runtime.stamp()]
        if queue=='tasks':fields+=',task_type';values.append(kind)
        else:fields+=',message_text,trigger_reason';values+=['Synthetic message','mention']
        return self.execute(f"INSERT INTO {table}({fields}) VALUES ({','.join('?' for _ in values)})",values)

    def log(self, ident, token='token', queue='tasks', calls=None):
        ai_runtime.record_attempt(queue,ident,token,{'model':'actual-model','calls':calls or []},'done')

    def test_idempotent_audit_snapshots_and_fallback_counts(self):
        ident=self.task()
        calls=[{'model':'first','provider':'google','context':{'contents':'exact compact input'},'error':'HTTP 429','status':'error'},
               {'model':'second','provider':'google','context':{'contents':'smaller input'},'response':{'answer':'<script>evil</script>'},'status':'received'}]
        self.log(ident,calls=calls);self.log(ident,calls=calls)
        ai_runtime.initialize();ai_runtime.initialize()
        result=dashboard.list_tasks(self.path)
        self.assertEqual(result['total'],1)
        self.assertEqual(sum(r['count'] for r in result['stats']),2)
        self.assertEqual(result['rows'][0]['actual_model'],'second')
        data=dashboard.detail(self.path,'tasks',ident)
        self.assertEqual(len(data['calls']),2)
        self.assertEqual(data['calls'][0]['context']['contents'],'exact compact input')
        self.execute("UPDATE ai_tasks SET prompt='retry prompt',result_text='different response' WHERE id=?",(ident,))
        self.assertEqual(dashboard.detail(self.path,'tasks',ident)['calls'][1]['response']['answer'],'<script>evil</script>')
        self.assertEqual(dashboard.list_tasks(self.path,model='first')['total'],1)
        self.assertEqual(dashboard.list_tasks(self.path,model='first')['stats'][0]['model'],'first')

    def test_table_previews_keep_final_answers_and_profile_names(self):
        response=self.task()
        self.log(response,calls=[{'model':'google','provider':'google','context':{},'response':{'candidates':[{'content':{'parts':[{'text':'API answer'}]}}]},'status':'received'}])
        self.execute("UPDATE ai_tasks SET result_text=? WHERE id=?", ('Final user answer',response))
        profile=self.task(kind='profile_update')
        self.execute("UPDATE ai_tasks SET result_text=?,payload_json=? WHERE id=?", (json.dumps({'display_name':'Snapshot name'}),json.dumps({'display_name':'Original name'}),profile))
        classifier=self.task(queue='type-checks')
        self.execute("UPDATE ai_type_checks SET result_type='imagegen' WHERE id=?", (classifier,))
        rows=dashboard.list_tasks(self.path)['rows']
        self.assertEqual(next(r for r in rows if r['queue']=='tasks' and r['id']==response)['response_preview'],'Final user answer')
        self.assertEqual(next(r for r in rows if r['queue']=='tasks' and r['id']==profile)['response_preview'],'Snapshot name')
        self.assertEqual(next(r for r in rows if r['queue']=='type-checks')['classification_type'],'imagegen')
        self.assertIn('candidates',dashboard.detail(self.path,'tasks',response)['calls'][0]['response'])
        self.execute("UPDATE ai_tasks SET result_text=NULL WHERE id=?", (response,))
        self.assertEqual(next(r for r in dashboard.list_tasks(self.path)['rows'] if r['queue']=='tasks' and r['id']==response)['response_preview'],'API answer')
        self.assertEqual(dashboard.answer_text(json.dumps({'candidates':[{'content':{'parts':[{'text':'private reasoning','thought':True},{'text':'Public answer'}]}}]})), 'Public answer')
        self.assertEqual(dashboard.answer_text(json.dumps({'usage':{'tokens':100}})), '')
        self.assertEqual(dashboard.answer_text('42'), '42')

    def test_all_queues_filters_period_and_missing_actual_model(self):
        a=self.task();self.task(chat=-13);self.task(queue='type-checks');self.task(queue='search-plans');self.task(date='2000-01-01T00:00:00')
        self.log(a)
        self.execute("UPDATE ai_tasks SET response_message_id=789 WHERE id=?",(a,))
        data=dashboard.list_tasks(self.path)
        self.assertEqual(data['total'],4)
        self.assertEqual(next(r for r in data['rows'] if r['queue']=='tasks' and r['id']==a)['response_message_id'],789)
        self.assertEqual({r['queue'] for r in data['rows']},set(ai_runtime.TABLES))
        self.assertTrue(any(r['actual_model'] is None for r in data['rows']))
        self.assertEqual(dashboard.list_tasks(self.path,chat=-13)['total'],1)
        self.assertEqual(dashboard.list_tasks(self.path,kind='type_check')['total'],1)
        self.assertEqual(dashboard.list_tasks(self.path,model='planned-local')['total'],0)
        self.assertEqual(sum(s['count'] for s in data['stats']),1)
        self.assertEqual(data['stats'][0]['historical'],1)
        self.assertEqual(dashboard.list_tasks(self.path,start='2000-01-01',end='2000-01-01')['total'],1)
        old=dashboard.list_tasks(self.path,start='2000-01-01',end='2000-01-01')
        self.assertEqual(old['rows'][0]['actual_model'],'gemma4:e4b')
        self.assertEqual(old['rows'][0]['assumed_model'],1)
        self.assertEqual(old['stats'][0]['count'],1)
        self.assertEqual(dashboard.list_tasks(self.path,start='2000-01-01',end='2000-01-01',model='gemma4:e4b')['total'],1)
        with self.assertRaises(HTTPException):dashboard.list_tasks(self.path,start='bad')
        with self.assertRaises(HTTPException):dashboard.detail(self.path,'not-table',1)

    def test_pagination_and_preview_limit(self):
        for user in range(55):self.task(user=user)
        self.execute("UPDATE ai_tasks SET prompt=?",('x'*5000,))
        data=dashboard.list_tasks(self.path,page=2)
        self.assertEqual(data['pages'],2);self.assertEqual(len(data['rows']),5)
        self.assertTrue(all(len(r['context_preview'])==220 for r in data['rows']))
        self.assertEqual(dashboard.list_tasks(self.path,page=999)['page'],2)

    def profile(self, day, chat=-12, user=123):
        ident=self.task('profile_update',chat,user)
        self.execute('INSERT INTO ai_profiles(user_id,chat_id,profile_date,status,profile_json,window_start,window_end,model,task_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                     (user,chat,day,'done',json.dumps({'summary':day}),day,day,'planned',ident,ai_runtime.stamp(),ai_runtime.stamp()))
        return ident

    def test_profile_history_is_scoped_and_previous_snapshot(self):
        first=self.profile('2026-09-28');current=self.profile('2026-09-29');last=self.profile('2026-09-30')
        self.profile('2026-09-27',chat=-999);self.profile('2026-09-27',user=999)
        data=dashboard.history(self.path,'tasks',current)
        self.assertEqual([x['task_id'] for x in data['items']],[first,current,last])
        self.assertEqual(data['index'],1)
        self.assertEqual(data['previous_profile'],{'summary':'2026-09-28'})
        self.assertIsNone(dashboard.history(self.path,'tasks',first)['previous_profile'])

    def test_summary_history_receipts_and_no_cross_chat(self):
        ids=[]
        for day in range(1,5):
            ident=self.task('chat_summary');ids.append(ident)
            self.execute('INSERT INTO ai_summary(chat_id,task_id,status,summary_text,window_start,window_end,model,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                         (-12,ident,'done',f'Summary {day}',f'2026-09-0{day}',f'2026-09-0{day}','planned',ai_runtime.stamp(),ai_runtime.stamp()))
        result=dashboard.history(self.path,'tasks',ids[1]);self.assertEqual(result['index'],1);self.assertEqual(len(result['items']),4)
        self.log(ids[0]);self.execute('INSERT INTO ai_receipts VALUES (?,?,?,?,?,?)',('token','w','tasks',ids[0],ai_runtime.stamp(),json.dumps({'final_task_id':456})))
        row=next(x for x in dashboard.list_tasks(self.path)['rows'] if x['id']==ids[0])
        self.assertEqual(row['receipt']['final_task_id'],456)

    def test_auth_is_required_for_every_sensitive_route(self):
        app=FastAPI()
        def session(request):
            if not request.cookies.get('test'):raise HTTPException(401)
            return {'telegram_user_id':int(request.cookies['test'])}
        dashboard.register(app,Mock(),self.path,session,{123})
        client=TestClient(app)
        for path in ['/api/ai-dashboard/tasks','/api/ai-dashboard/detail/tasks/1','/api/ai-dashboard/history/tasks/1','/api/ai-dashboard/avatar/tasks/1']:
            self.assertEqual(client.get(path).status_code,401)
            client.cookies.set('test','456');self.assertEqual(client.get(path).status_code,403);client.cookies.clear()
        client.cookies.set('test','123')
        response=client.get('/api/ai-dashboard/tasks')
        self.assertEqual(response.status_code,200);self.assertEqual(response.headers['cache-control'],'no-store')
        ident=self.task('profile_update')
        photo=Path(self.tmp.name)/'synthetic.jpg';photo.write_bytes(b'synthetic-test-file')
        with patch('web.ai_dashboard.AvatarCache.get',return_value=photo) as lookup:
            response=client.get(f'/api/ai-dashboard/avatar/tasks/{ident}')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.headers['content-type'],'image/jpeg')
            self.assertEqual(response.headers['cache-control'],'private, no-store')
            lookup.assert_called_once_with(123)
        with patch('web.ai_dashboard.AvatarCache.get') as lookup:
            other=self.task('response')
            self.assertEqual(client.get(f'/api/ai-dashboard/avatar/tasks/{other}').status_code,404)
            lookup.assert_not_called()

    def test_audit_excludes_headers_and_sanitizes_errors(self):
        ai_audit.begin()
        response=Mock(ok=False,status_code=429)
        response.json.return_value={'error':'secret-echo'}
        with patch('ai_http.post',return_value=response):
            ai_audit.post('https://example.test',provider='google',model='m',headers={'key':'secret-key'},json={'prompt':'input'})
        calls=ai_audit.take()
        self.assertEqual(calls[0]['context'],{'prompt':'input'})
        self.assertNotIn('secret',json.dumps(calls))

    def test_token_counting_is_not_a_generation_call(self):
        from ai_providers import google_request
        response=Mock(ok=True,status_code=200)
        response.json.return_value={'totalTokens':5}
        ai_audit.begin()
        with patch('ai_http.post',return_value=response):
            google_request('synthetic','countTokens',{'contents':'count'},'test-key',2)
            google_request('synthetic','generateContent',{'contents':'generate'},'test-key',2)
        calls=ai_audit.take()
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0]['context']['contents'],'generate')

    def test_worker_sends_failed_call_snapshot_when_all_sources_fail(self):
        from ai_worker_client import Worker
        from ai_providers import ProviderUnavailable
        worker=Worker('groq',['tasks'])
        worker.request=Mock(return_value={'status':'waiting'})
        def failed(task,timeout):
            ai_audit.post('https://example.test',provider='google',model='synthetic',json={'prompt':'exact input'})
            raise ProviderUnavailable('temporary failure')
        with patch('ai_http.post',return_value=Mock(ok=False,status_code=500)),patch('ai_worker_client.call_external',side_effect=failed):
            worker.process({'queue':'tasks','id':1,'lease_token':'lease','task_type':'response'})
        data=worker.request.call_args.args[1]
        self.assertEqual(data['error_kind'],'unavailable')
        self.assertEqual(data['metadata']['calls'][0]['model'],'synthetic')
        self.assertEqual(data['metadata']['calls'][0]['context']['prompt'],'exact input')


if __name__=='__main__':unittest.main()
