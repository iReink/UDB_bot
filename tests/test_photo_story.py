import base64
from contextlib import closing
import io
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

import ai_tasks
import ai_runtime as rt
import ai_providers
import ai_audit
import photo_story as story


class PhotoStoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=patch.object(ai_tasks,'DB_FILE',Path(self.temp.name)/'test.db')
        self.db.start(); rt.initialize()
        rt.heartbeat('pc','local',['tasks']);rt.heartbeat('normal','groq',['tasks'])
        rt.heartbeat('vps','groq',['tasks'],task_types=list(story.KINDS))

    def tearDown(self):
        self.db.stop();self.temp.cleanup()

    def message(self,ident=1,group='album',chat=42):
        return SimpleNamespace(chat=SimpleNamespace(id=chat,type='private' if chat>0 else 'supergroup'),
          from_user=SimpleNamespace(id=99),message_id=ident,media_group_id=group,
          photo=[SimpleNamespace(file_id='synthetic-'+str(ident))],caption='Кафе')

    def seal(self):
        with closing(story.connection()) as conn,conn:
            conn.execute('UPDATE ai_photo_story_batches SET touched_at=?',(time.time()-5,))
        return story.seal_ready()

    def test_album_duplicate_restart_late_photo_and_group_isolation(self):
        bid=story.enqueue(self.message())
        self.assertIsNone(story.enqueue(self.message()))
        self.assertEqual(story.enqueue(self.message(2)),bid)
        self.assertIsNone(story.enqueue(self.message(3,chat=-42)))
        rt.initialize();self.assertEqual(len(self.seal()),1)
        self.assertEqual(story.seal_ready(),[])
        task=rt.claim('tasks','vps')
        self.assertEqual(len(task['payload']['photos']),2)
        self.assertNotEqual(story.enqueue(self.message(3)),bid)

    def test_scoped_external_routing_off_and_defer(self):
        story.enqueue(self.message());self.seal()
        self.assertIsNone(rt.claim('tasks','pc'))
        self.assertIsNone(rt.claim('tasks','normal'))
        task=rt.claim('tasks','vps')
        rt.defer('tasks',task['id'],'vps',task['lease_token'],'quota',90,False)
        with closing(story.connection()) as conn:
            row=conn.execute('SELECT next_provider,retry_at FROM ai_tasks WHERE id=?',(task['id'],)).fetchone()
        self.assertIsNone(row[0]);self.assertGreater(row[1],rt.stamp(8))
        self.assertLessEqual(row[1],rt.stamp(11))
        rt.set_mode(42,'off');self.assertIsNone(rt.claim('tasks','vps'))
        self.assertIsNone(story.enqueue(self.message(4)))

    def test_saved_analysis_unique_merge_and_delivery(self):
        bid=story.enqueue(self.message());self.seal();task=rt.claim('tasks','vps')
        with closing(story.connection()) as conn:
            parent=dict(conn.execute('SELECT * FROM ai_tasks WHERE id=?',(task['id'],)).fetchone())
        sent=[]
        send=lambda *args,**kwargs: sent.append((args,kwargs)) or 444
        result=story.complete(parent,'На фото стол и чашки. Похоже на встречу в кафе.',None,send)
        duplicate=story.complete(parent,'На фото стол и чашки. Похоже на встречу в кафе.',None,send)
        self.assertEqual(result['final_task_id'],duplicate['final_task_id']);self.assertEqual(sent,[])
        child=rt.claim('tasks','vps')
        self.assertEqual(child['task_type'],'photo_story_merge')
        self.assertIn('стол и чашки',child['prompt'])
        with closing(story.connection()) as conn:
            child=dict(conn.execute('SELECT * FROM ai_tasks WHERE id=?',(child['id'],)).fetchone())
        self.assertEqual(story.complete(child,'**Кофейный совет**\n\nЧашки готовы к встрече.',None,send)['status'],'done')
        self.assertIn('<b>Кофейный совет</b>',sent[0][0][1])
        self.assertEqual(sent[0][1]['reply_to_message_id'],1)
        with closing(story.connection()) as conn:
            self.assertEqual(conn.execute('SELECT status FROM ai_photo_story_batches WHERE id=?',(bid,)).fetchone()[0],'done')

    def test_length_retry_and_max_pending(self):
        for ident in range(1,4):story.enqueue(self.message(ident,group=None))
        self.assertEqual(story.enqueue(self.message(4,group=None)),'busy')
        self.seal();task=rt.claim('tasks','vps')
        with closing(story.connection()) as conn:
            task=dict(conn.execute('SELECT * FROM ai_tasks WHERE id=?',(task['id'],)).fetchone())
        self.assertEqual(story.complete(task,'x'*6001,None,lambda *a,**k: self.fail())['status'],'retry')

    def test_vision_limits_and_fallback_chain(self):
        for model in story.VISION_MODELS:
            self.assertEqual(rt.model_limits(model)['RPD'],20)
            self.assertEqual(rt.model_limits(model)['RPM'],5)
        calls=[]
        def google(task,timeout,model):
            calls.append(model)
            if len(calls)<3:raise ai_providers.ProviderUnavailable('quota',60,False)
            return 'Synthetic vision',{'model':model}
        with patch.object(story,'image_parts',return_value=[{'text':'frame'}]),patch.object(ai_providers,'call_google',side_effect=google):
            output,meta=ai_providers.call_external({'task_type':'photo_story','prompt':'p','payload':{'photos':[{}]}},30)
        self.assertEqual(calls,list(story.VISION_MODELS[:3]));self.assertEqual(output,'Synthetic vision')

    def test_multimodal_payload_count_and_audit_redaction(self):
        from PIL import Image
        data=io.BytesIO();Image.new('RGB',(32,32),'red').save(data,format='JPEG')
        image={'inlineData':{'mimeType':'image/jpeg','data':base64.b64encode(data.getvalue()).decode()}}
        task={'task_type':'photo_story','prompt':'Сравни кадры','_image_parts':[image,image]}
        payloads=[]
        def request(model,method,payload,*args,**kwargs):
            payloads.append((method,payload))
            if method=='countTokens':return {'totalTokens':500}
            return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'Красные кадры.'}]}}],
                    'usageMetadata':{'promptTokenCount':500,'candidatesTokenCount':20}}
        with patch.dict(os.environ,{'GEMINI_API_KEY':'synthetic'}),patch.object(ai_providers,'google_request',side_effect=request):
            text,_=ai_providers.call_google(task,30,story.VISION_MODELS[0])
        self.assertEqual(text,'Красные кадры.')
        parts=payloads[-1][1]['contents'][0]['parts'];self.assertEqual(len(parts),3)
        redacted=json.dumps(ai_audit.snapshot(payloads[-1][1]))
        self.assertNotIn(image['inlineData']['data'],redacted);self.assertIn('encoded_bytes',redacted)

    def test_retry_receipt_fences_late_duplicate(self):
        story.enqueue(self.message());self.seal();task=rt.claim('tasks','vps')
        self.assertIsNone(rt.accept_result('tasks',task['id'],'vps',task['lease_token']))
        with closing(story.connection()) as conn:
            parent=dict(conn.execute('SELECT * FROM ai_tasks WHERE id=?',(task['id'],)).fetchone())
        result=story.complete(parent,'x'*6001,None,lambda *a,**k: self.fail())
        rt.finish_receipt(task['lease_token'],result)
        self.assertEqual(rt.accept_result('tasks',task['id'],'vps',task['lease_token']),result)

    def test_web_pipeline_delivery_and_duplicate_result(self):
        from fastapi.testclient import TestClient
        from web import server
        headers={'Authorization':'Bearer synthetic'}
        story.enqueue(self.message());self.seal()
        with patch.object(server,'AI_WORKER_TOKEN','synthetic'),patch.object(server,'_send_telegram_message',return_value=123) as send:
            client=TestClient(server.app)
            beat=client.post('/api/ai/workers/heartbeat',headers=headers,json={
                'worker_id':'photo-api','provider':'groq','queues':['tasks'],'task_types':list(story.KINDS)})
            self.assertEqual(beat.status_code,200,beat.text)
            parent=client.get('/api/ai/tasks/next',headers=headers,params={'worker_id':'photo-api'}).json()['task']
            def deliver(task,text):
                return client.post('/api/ai/tasks/'+str(task['id'])+'/result',headers=headers,json={
                    'worker_id':'photo-api','lease_token':task['lease_token'],'output':text,
                    'metadata':{'model':'synthetic','calls':[]}})
            result=deliver(parent,'Видны стол и две чашки. Похоже на встречу в кафе.')
            self.assertEqual(result.status_code,200,result.text)
            self.assertIn('final_task_id',result.json())
            child=client.get('/api/ai/tasks/next',headers=headers,params={'worker_id':'photo-api'}).json()['task']
            result=deliver(child,'**Уютная встреча**\n\nЧашки уже готовы выслушать все новости.')
            self.assertEqual(result.json()['status'],'done',result.text)
            self.assertEqual(deliver(child,'Повтор').json(),result.json());self.assertEqual(send.call_count,1)

    def test_telegram_download_is_bounded_and_resized_in_memory(self):
        from PIL import Image
        data=io.BytesIO();Image.new('RGB',(1800,900),'red').save(data,format='JPEG')
        info=Mock(status_code=200);info.json.return_value={'result':{'file_path':'synthetic.jpg','file_size':len(data.getvalue())}}
        download=Mock(status_code=200);download.iter_content.return_value=[data.getvalue()]
        context=Mock();context.__enter__=Mock(return_value=download);context.__exit__=Mock(return_value=False)
        with patch.dict(os.environ,{'BOT_TOKEN':'synthetic'}),patch.object(story.requests,'post',return_value=info),patch.object(story.requests,'get',return_value=context):
            parts=story.image_parts([{'file_id':'synthetic','caption':'Кафе'}])
        image=Image.open(io.BytesIO(base64.b64decode(parts[1]['inlineData']['data'])))
        self.assertEqual(image.size,(1280,640));image.close()
        self.assertIn('Кафе',parts[0]['text'])

    def test_service_tables_are_not_available_to_user_sql(self):
        for table in ('ai_photo_story_batches','ai_photo_story_inputs'):
            with self.assertRaises(ai_tasks.TextToSqlError):
                ai_tasks.validate_text_to_sql('SELECT * FROM '+table,chat_id=-42)


if __name__=='__main__':unittest.main()
