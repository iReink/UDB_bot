import json
import gc
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

import db
import auth_code
import cepen_avatar
import cepen_ai_avatar
from web import cepen_api as api


class CepenApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root/'test.db'
        self.secret = 'artificial-test-secret'
        self.sql = []
        self.db_patch = patch.object(db, 'DB_FILE', str(self.path))
        self.db_patch.start()
        self.avatar_patch = patch.object(cepen_ai_avatar, 'ROOT', self.root/'ai')
        self.avatar_patch.start()
        (self.root/'ai').mkdir()
        self.cartoon = self.root/'cartoon.png'
        Image.new('RGB', (8, 8), 'blue').save(self.cartoon)
        self.render = patch.object(cepen_avatar, 'render_avatar', return_value=self.cartoon).start()
        with closing(self.connect()) as c, c:
            c.executescript('''CREATE TABLE users(user_id INTEGER,chat_id INTEGER,name TEXT,cepen REAL,cepen_name TEXT,cepen_profession TEXT,PRIMARY KEY(chat_id,user_id));
            CREATE TABLE settings(chat_id INTEGER,name TEXT,value INTEGER,PRIMARY KEY(chat_id,name));
            CREATE TABLE cepen_scratches(chat_id INTEGER,owner_id INTEGER,scratch_date TEXT);
            CREATE TABLE cepen_ai_avatars(chat_id INTEGER,user_id INTEGER,level INTEGER,skin TEXT,path TEXT,enabled INTEGER,generated_at TEXT,PRIMARY KEY(chat_id,user_id));''')
            c.executemany('INSERT INTO users VALUES(?,?,?,?,?,?)', [(1,-42,'Owner',42.25,'Worm','designer'),
                (1,-43,'Owner',0,None,None),(2,-44,'Other',12,None,None),(1,1,'Private',0,None,None)])
        self.app = FastAPI()
        api.register_cepen_api(self.app, connect=self.connect, consume_code=auth_code.consume_auth_code,
                              secret=lambda:self.secret, chat_label=lambda u,c:f'Chat {c}')
        self.client = TestClient(self.app)
        token, _ = api.issue_token(1, self.secret)
        self.headers = {'Authorization': 'Bearer '+token}
        self.sql.clear()

    def tearDown(self):
        self.client.close()
        patch.stopall()
        gc.collect()  # Legacy auth_code connection contexts commit but close during GC.
        self.tmp.cleanup()

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.set_trace_callback(self.sql.append)
        return conn

    def state(self, chat=-42, **kwargs):
        return self.client.get('/api/v1/cepen', params={'chat_id':chat, **kwargs}, headers=self.headers)

    def test_real_code_exchange_single_use_and_no_extra_data(self):
        issued = auth_code.issue_auth_code(1)
        response = self.client.post('/api/v1/auth/code', json={'code':issued.code})
        self.assertEqual(200,response.status_code)
        value=response.json()
        self.assertEqual('cepen:read',value['scope']);self.assertEqual(api.TOKEN_TTL,value['expires_in'])
        self.assertEqual('no-store',response.headers['cache-control'])
        self.assertEqual(401,self.client.post('/api/v1/auth/code',json={'code':issued.code}).status_code)
        chats=self.client.get('/api/v1/chats',headers={'Authorization':'Bearer '+value['access_token']})
        self.assertEqual([-43,-42],[c['chat_id'] for c in chats.json()['chats']])
        self.assertNotIn('balance',chats.text)
        self.assertEqual(422,self.client.post('/api/v1/auth/code',json={'code':'1234','user_id':2}).status_code)

    def test_owner_chat_access_and_all_basic_fields(self):
        response=self.state(user_id=2)
        self.assertEqual(200,response.status_code)
        value=response.json()
        self.assertEqual({'schema_version','user_id','owner_name','chat_id','chat_name','mechanic_enabled','has_cepen','name','length_cm','appearance_level','profession_id','profession_title','profession_selected','avatar_url','mood'},set(value))
        self.assertEqual(1,value['user_id']);self.assertEqual(42.25,value['length_cm'])
        self.assertEqual(3,value['appearance_level']);self.assertEqual('designer',value['profession_id'])
        self.assertEqual('подавлен',value['mood'])
        self.assertTrue(value['mechanic_enabled']);self.assertTrue(value['profession_selected'])
        self.assertNotIn('path',value);self.assertNotIn('sits',value)
        self.assertEqual(403,self.state(-44).status_code)
        self.assertEqual(422,self.state(1).status_code)
        self.assertEqual(422,self.state(-(2**63)-1).status_code)
        self.assertEqual(422,self.client.get('/api/v1/cepen',headers=self.headers).status_code)
        self.assertFalse(any(q.lstrip().split(' ',1)[0].upper() in ('CREATE','ALTER','INSERT','UPDATE','DELETE') for q in self.sql))
        self.render.assert_not_called()

    def test_tokens_missing_tampered_expired_wrong_scope(self):
        for url in ('/api/v1/chats','/api/v1/cepen?chat_id=-42','/api/v1/cepen/avatar?chat_id=-42'):
            self.assertEqual(401,self.client.get(url).status_code)
            self.assertEqual(401,self.client.get(url,headers={'Authorization':'Bearer bad'}).status_code)
        bad=self.headers['Authorization'][:-1]+('a' if self.headers['Authorization'][-1]!='a' else 'b')
        self.assertEqual(401,self.client.get('/api/v1/chats',headers={'Authorization':bad}).status_code)
        with patch.object(api.time,'time',return_value=1000):token,_=api.issue_token(1,self.secret)
        with patch.object(api.time,'time',return_value=1000+api.TOKEN_TTL):
            self.assertEqual(401,self.client.get('/api/v1/chats',headers={'Authorization':'Bearer '+token}).status_code)
        with patch.object(api,'SCOPE','other'):token,_=api.issue_token(1,self.secret)
        self.assertEqual(401,self.client.get('/api/v1/chats',headers={'Authorization':'Bearer '+token}).status_code)
        self.assertEqual(401,self.client.get('/api/v1/chats',cookies={'udb_web_session':'web-token'}).status_code)

    def test_no_cepen_disabled_mechanic_and_no_profession(self):
        value=self.state(-43).json()
        self.assertFalse(value['has_cepen']);self.assertEqual(0,value['length_cm'])
        for key in ('name','appearance_level','profession_id','profession_title','avatar_url','mood'):self.assertIsNone(value[key])
        self.assertFalse(value['profession_selected'])
        self.assertEqual(404,self.client.get('/api/v1/cepen/avatar?chat_id=-43',headers=self.headers).status_code)
        with closing(self.connect()) as c,c:
            c.execute("INSERT INTO settings VALUES(-42,'enable_cepen',0)")
            c.execute('UPDATE users SET cepen_profession=NULL WHERE user_id=1 AND chat_id=-42')
        value=self.state().json()
        self.assertFalse(value['mechanic_enabled']);self.assertTrue(value['has_cepen'])
        self.assertEqual('Без профессии',value['profession_title']);self.assertFalse(value['profession_selected'])

    def test_mood_thresholds_from_today_only(self):
        day=datetime.now(api.TZ).date().isoformat()
        for number,expected in ((0,'подавлен'),(1,'удовлетворён'),(9,'удовлетворён'),(10,'счастлив')):
            with closing(self.connect()) as c,c:
                c.execute('DELETE FROM cepen_scratches')
                c.executemany('INSERT INTO cepen_scratches VALUES(?,?,?)',[(-42,1,day)]*number+[(-42,1,'2000-01-01')]*10+[(-44,1,day)]*10)
            self.assertEqual(expected,self.state().json()['mood'])

    def test_avatar_fallback_ai_missing_disabled_stale_and_authorization(self):
        fallback=self.state().json()['avatar_url']
        result=self.client.get(fallback,headers=self.headers)
        self.assertEqual(200,result.status_code);self.assertEqual(self.cartoon.read_bytes(),result.content)
        self.assertIn('private',result.headers['cache-control'])
        self.assertEqual('Authorization',result.headers['vary'])
        ai=self.root/'ai'/(cepen_ai_avatar.AVATAR_REVISION+'_test.png')
        Image.new('RGB',(8,8),'red').save(ai)
        with closing(self.connect()) as c,c:
            c.execute('INSERT INTO cepen_ai_avatars VALUES(?,?,?,?,?,?,?)',(-42,1,3,'designer',str(ai),1,'now'))
        chosen=self.state().json()['avatar_url']
        self.assertNotEqual(fallback,chosen)
        self.assertEqual(ai.read_bytes(),self.client.get(chosen,headers=self.headers).content)
        self.assertEqual(401,self.client.get(chosen).status_code)
        self.assertEqual(403,self.client.get('/api/v1/cepen/avatar?chat_id=-44',headers=self.headers).status_code)
        with closing(self.connect()) as c,c:c.execute('UPDATE cepen_ai_avatars SET enabled=0')
        self.assertEqual(fallback,self.state().json()['avatar_url'])
        with closing(self.connect()) as c,c:c.execute('UPDATE cepen_ai_avatars SET enabled=1,level=1')
        self.assertEqual(fallback,self.state().json()['avatar_url'])
        with closing(self.connect()) as c,c:c.execute('UPDATE cepen_ai_avatars SET level=3')
        ai.unlink()
        self.assertEqual(fallback,self.state().json()['avatar_url'])

    def test_auth_rate_limit_and_code_string(self):
        for _ in range(5):self.assertEqual(401,self.client.post('/api/v1/auth/code',json={'code':'1234'}).status_code)
        response=self.client.post('/api/v1/auth/code',json={'code':'1234'})
        self.assertEqual(429,response.status_code);self.assertEqual('60',response.headers['retry-after'])
        app=FastAPI();consume=unittest.mock.Mock(return_value=1)
        api.register_cepen_api(app,connect=self.connect,consume_code=consume,secret=lambda:self.secret,chat_label=lambda u,c:'Chat')
        with TestClient(app) as client:
            self.assertEqual(200,client.post('/api/v1/auth/code',json={'code':'0007'}).status_code)
        consume.assert_called_once_with('0007')
        limits=api.LoginLimits()
        with patch.object(api.time,'monotonic',return_value=100):
            for i in range(30):limits.check(str(i))
            with self.assertRaises(HTTPException) as error:limits.check('new')
            self.assertEqual(429,error.exception.status_code)
        with patch.object(api.time,'monotonic',return_value=161):limits.check('new')
