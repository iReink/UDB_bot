import asyncio
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock,patch
import ai_tasks
import ai_runtime
import imagegen
import imagegen_bot
from ai_providers import ProviderUnavailable

class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=patch.object(ai_tasks,'DB_FILE',Path(self.tmp.name)/'test.db');self.db.start()
        ai_runtime.initialize()
        ident=imagegen.create_task(chat_id=-42,user_id=1,request_message_id=9,user_query='Нарисуй гусей')
        with closing(imagegen.connect()) as c:
            self.task=dict(c.execute('SELECT * FROM ai_tasks WHERE id=?',(ident,)).fetchone())
        self.bot=SimpleNamespace(send_message=AsyncMock(),send_photo=AsyncMock(return_value=SimpleNamespace(message_id=99)))
    def tearDown(self):self.db.stop();self.tmp.cleanup()
    def test_unavailable_preparation_still_generates_and_delivers(self):
        with patch('ai_providers.call_external',side_effect=ProviderUnavailable('all unavailable',kind='daily')) as prepare,patch.object(imagegen,'generate',return_value=b'image') as generate:
            asyncio.run(imagegen_bot.run_task(self.bot,self.task))
        self.assertEqual(prepare.call_count,1);generate.assert_called_once()
        self.assertEqual(generate.call_args.args[1],self.task['prompt'])
        self.bot.send_photo.assert_awaited_once()
        with closing(imagegen.connect()) as c:
            self.assertEqual(c.execute('SELECT status FROM ai_tasks').fetchone()[0],'done')
            self.assertEqual(c.execute('SELECT prepared_prompt FROM ai_imagegen_jobs').fetchone()[0],self.task['prompt'])
    def test_successful_preparation_and_audit_failure_do_not_block_generation(self):
        with patch('ai_providers.call_external',return_value=('A flock of geese',{})),patch('ai_runtime.record_attempt',side_effect=RuntimeError('audit unavailable')),patch.object(imagegen,'generate',return_value=b'image') as generate:
            asyncio.run(imagegen_bot.run_task(self.bot,self.task))
        self.assertEqual(generate.call_args.args[1],'A flock of geese')
        self.bot.send_photo.assert_awaited_once()
    def test_photo_download_failure_has_durable_bounded_retries(self):
        with patch.object(imagegen_bot,'prepare',side_effect=ProviderUnavailable('download failed')),patch.object(imagegen,'generate') as generate:
            for _ in range(3):asyncio.run(imagegen_bot.run_task(self.bot,self.task))
        generate.assert_not_called()
        with closing(imagegen.connect()) as c:
            r=c.execute('SELECT status,transport_attempt FROM ai_tasks').fetchone()
        self.assertEqual(tuple(r),('failed',2))
        self.assertEqual(self.bot.send_message.await_count,2)
    def test_old_schema_migrates_twice(self):
        import sqlite3
        with sqlite3.connect(':memory:') as c:
            c.execute("CREATE TABLE ai_imagegen_batches(id INTEGER PRIMARY KEY,chat_id INTEGER,user_id INTEGER,group_key TEXT,first_message_id INTEGER,private INTEGER,addressed INTEGER,touched_at REAL,status TEXT,created_at REAL)")
            c.execute("INSERT INTO ai_imagegen_batches VALUES(1,-42,1,'old',9,0,1,0,'typed',0)")
            imagegen.ensure_schema(c);imagegen.ensure_schema(c)
            self.assertEqual(c.execute('SELECT reply_photos_json FROM ai_imagegen_batches').fetchone()[0],'[]')

    def test_fallback_keeps_both_references_in_order(self):
        import json
        refs=[dict(file_id='base',message_id=5,source='reply'),dict(file_id='girl',message_id=9)]
        self.task['payload_json']=json.dumps({'photos':refs})
        with patch('photo_story.image_parts',return_value=['parts']) as download,patch('ai_providers.call_external',side_effect=ProviderUnavailable('503')),patch.object(imagegen,'reference_images',return_value=([b'base',b'girl'],(1024,768))),patch.object(imagegen,'generate',return_value=b'image') as generate:
            asyncio.run(imagegen_bot.run_task(self.bot,self.task))
        self.assertEqual(download.call_args.args[0],refs)
        self.assertEqual(generate.call_args.args[2],[b'base',b'girl'])
        self.assertIn('image 1: photo from the replied-to message',generate.call_args.args[1])
        self.assertIn('image 2: newly attached photo',generate.call_args.args[1])
