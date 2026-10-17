import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
from uuid import uuid4
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
import cepen
import db
from web import cepen_api as api
import test_cepen as fixtures
CHAT = fixtures.CHAT


class ScratchApiTests(unittest.TestCase):
    def setUp(self):
        fixtures.CepenTests.setUp(self)
        with closing(db.get_connection()) as c, c:
            c.execute('UPDATE users SET cepen=42 WHERE user_id=1 AND chat_id=?', (CHAT,))
        self.secret = 'artificial-test-secret'
        self.app = FastAPI()
        api.register_cepen_api(self.app, connect=db.get_connection, consume_code=lambda code: 1,
                               secret=lambda: self.secret, chat_label=lambda u,c: 'Test')
        self.client = TestClient(self.app)
        self.headers = {'Authorization': 'Bearer '+api.issue_token(1,self.secret)[0]}

    def tearDown(self):
        self.client.close()
        fixtures.CepenTests.tearDown(self)

    def call(self, count=1, key=None, chat=CHAT, headers=None):
        return self.client.post('/api/v1/cepen/scratch',json={'chat_id':chat,'count':count,
            'request_id':key or str(uuid4())},headers=self.headers if headers is None else headers)

    def counts(self):
        with closing(db.get_connection()) as c:
            return tuple(c.execute('SELECT (SELECT COUNT(*) FROM cepen_scratches),'
                '(SELECT COUNT(*) FROM sit_ledger WHERE action_code=?)', ('cepen_scratch_reward',)).fetchone())

    def test_batch_shared_telegram_limit_and_ledger(self):
        day=datetime.now(api.TZ).date().isoformat()
        cepen.scratch(CHAT,1,1,'telegram',date_key=day)
        result=self.call(6)
        self.assertEqual(200,result.status_code)
        v=result.json()
        self.assertEqual((4,2,'user_limit',5,0),tuple(v[k] for k in
            ('accepted_count','rejected_count','reason','owner_count','owner_remaining')))
        self.assertEqual('удовлетворён',v['mood'])
        self.assertEqual((5,5),self.counts())
        with closing(db.get_connection()) as c:
            rewards=c.execute("SELECT SUM(reward) FROM cepen_scratches WHERE callback_query_id LIKE 'api:%'").fetchone()[0]
        self.assertEqual(round(rewards,3),v['reward_sits'])
        self.assertEqual('user_limit',cepen.scratch(CHAT,1,1,'later',date_key=day).status)

    def test_duplicate_conflict_restart_and_next_date(self):
        key=str(uuid4());v=self.call(3,key).json()
        self.assertEqual(v,self.call(3,key).json())
        self.assertEqual(409,self.call(4,key).status_code)
        tomorrow=datetime.now(api.TZ)+timedelta(days=1)
        with patch.object(api,'datetime') as clock:
            clock.now.return_value=tomorrow
            self.assertEqual(v,self.call(3,key).json())
            self.assertEqual(2,self.call(2).json()['accepted_count'])
        self.assertEqual((5,5),self.counts())
        with TestClient(self.app) as client:
            r=client.post('/api/v1/cepen/scratch',headers=self.headers,
                json={'chat_id':CHAT,'count':3,'request_id':key})
            self.assertEqual(v,r.json())

    def test_validation_auth_no_cepen_disabled(self):
        self.assertEqual(401,self.call(headers={}).status_code)
        with patch.object(api,'WRITE_SCOPE',''):
            old=api.issue_token(1,self.secret)[0]
        self.assertEqual(200,self.client.get('/api/v1/chats',headers={'Authorization':'Bearer '+old}).status_code)
        self.assertEqual(403,self.call(headers={'Authorization':'Bearer '+old}).status_code)
        self.assertEqual(403,self.call(chat=-999).status_code)
        for count in (0,-1,51,1.5,True,'2'):
            self.assertEqual(422,self.call(count).status_code)
        self.assertEqual(422,self.call(key='bad').status_code)
        with closing(db.get_connection()) as c,c:
            c.execute("INSERT OR REPLACE INTO settings VALUES(?,'enable_cepen',0)",(CHAT,))
        self.assertEqual(409,self.call().status_code)
        with closing(db.get_connection()) as c,c:
            c.execute("DELETE FROM settings WHERE name='enable_cepen'")
            c.execute('UPDATE users SET cepen=0 WHERE user_id=1')
        self.assertEqual(404,self.call().status_code)
        self.assertEqual((0,0),self.counts())

    def test_rollback_whole_batch_and_retry(self):
        original=db.apply_sit_change;calls=0
        def fail_second(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2:raise RuntimeError('artificial failure')
            return original(*args,**kwargs)
        key=str(uuid4())
        with patch.object(db,'apply_sit_change',side_effect=fail_second):
            with self.assertRaises(RuntimeError):self.call(3,key)
        self.assertEqual((0,0),self.counts())
        self.assertEqual(3,self.call(3,key).json()['accepted_count'])

    def test_concurrent_duplicate_and_distinct_batches(self):
        key=str(uuid4())
        with ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(lambda _:self.call(3,key).json(),range(3)))
        self.assertTrue(all(v==results[0] for v in results))
        self.assertEqual((3,3),self.counts())
        with ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(lambda _:self.call(3).json(),range(3)))
        self.assertEqual(2,sum(v['accepted_count'] for v in results))
        self.assertEqual((5,5),self.counts())

    def test_total_limit_and_migration(self):
        db.initialize_db();db.initialize_db()
        day=datetime.now(api.TZ).date().isoformat()
        with closing(db.get_connection()) as c,c:
            c.executemany('INSERT INTO cepen_scratches VALUES(?,?,?,?,?,?,?)',
                [(f'other-{i}',day,CHAT,1,100+i,0.1,day) for i in range(49)])
        v=self.call(4).json()
        self.assertEqual((1,3,'worm_limit',50,'счастлив'),tuple(v[k] for k in
            ('accepted_count','rejected_count','reason','total_count','mood')))
        self.assertEqual(0,self.call().json()['accepted_count'])
