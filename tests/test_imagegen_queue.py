import os
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch
import ai_tasks
import ai_runtime as rt
import imagegen as gen
import imagegen_bot as bridge
import imagegen_hf as hf

class ImageQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=patch.object(ai_tasks,'DB_FILE',Path(self.tmp.name)/'test.db');self.db.start();rt.initialize();rt.set_mode(-42,'api')
    def tearDown(self):
        self.db.stop();self.tmp.cleanup()
    def test_empty_deferred_and_due_task_claim(self):
        self.assertIsNone(bridge.claim_image_task())
        ident=gen.create_task(chat_id=-42,user_id=1,request_message_id=1,user_query='Нарисуй кота')
        with closing(gen.connect()) as c,c:c.execute('UPDATE ai_tasks SET retry_at=? WHERE id=?',(rt.stamp(60),ident))
        self.assertIsNone(bridge.claim_image_task())
        with closing(gen.connect()) as c,c:c.execute('UPDATE ai_tasks SET retry_at=NULL WHERE id=?',(ident,))
        self.assertEqual(bridge.claim_image_task()['id'],ident)
        self.assertIsNone(bridge.claim_image_task())
    def test_four_photo_batches_are_accepted(self):
        for ident in range(1,5):
            message=SimpleNamespace(chat=SimpleNamespace(id=-42,type='supergroup'),from_user=SimpleNamespace(id=1,is_bot=False),message_id=ident,media_group_id=str(ident),reply_to_message=None,caption='Бот, нарисуй кота',photo=[SimpleNamespace(file_id='synthetic')])
            self.assertIsNone(gen.collect(message))
        with closing(gen.connect()) as c:self.assertEqual(c.execute('SELECT count(*) FROM ai_imagegen_batches').fetchone()[0],4)
    def test_hf_refusal_calls_flux(self):
        with patch.dict(os.environ,{'HF_TOKEN':'test'}),patch.object(hf,'generate',side_effect=hf.Unavailable('quota_exhausted')),patch.object(gen,'generate_cloudflare',return_value=b'image') as fallback:
            self.assertEqual(gen.generate({'id':1},'cat',[],(1024,1024)),b'image')
            fallback.assert_called_once()
    def test_hf_audit_failure_does_not_block_flux_fallback(self):
        with patch.dict(os.environ,{'HF_TOKEN':'test'}),patch.object(hf,'quota',return_value={'current':0}),patch.object(rt,'record_attempt',side_effect=RuntimeError('audit unavailable')),patch.object(gen,'generate_cloudflare',return_value=b'image') as fallback:
            self.assertEqual(gen.generate({'id':1},'cat',[],(1024,1024)),b'image')
            fallback.assert_called_once()
