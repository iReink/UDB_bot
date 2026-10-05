import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as Obj
from contextlib import closing
from unittest.mock import patch
import ai_tasks as tasks
import ai_runtime as rt
import ai_conversation as conversation

class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=patch.object(tasks,'DB_FILE',Path(self.tmp.name)/'test.db');self.db.start();rt.initialize()
        with closing(tasks.get_connection()) as c,c:
            c.execute('CREATE TABLE IF NOT EXISTS messages_reactions(chat_id INTEGER,message_id INTEGER,user_id INTEGER,message_text TEXT,reactions_count INTEGER DEFAULT 0,date TEXT,PRIMARY KEY(chat_id,message_id))')
            c.execute('CREATE TABLE IF NOT EXISTS users(chat_id INTEGER,user_id INTEGER,name TEXT,nick TEXT)')
            self.task=c.execute("INSERT INTO ai_tasks(task_type,status,model,prompt,chat_id,user_id,request_message_id,created_at,updated_at) VALUES('response','processing','test','test',-42,1,1,'2026-10-05','2026-10-05')").lastrowid
    def tearDown(self):self.db.stop();self.tmp.cleanup()
    def test_only_response_is_saved_idempotently_and_marked_self(self):
        result={'message_id':2,'from':{'id':999},'text':'Мой ответ'}
        conversation.save_reply(self.task,-42,result,'unused');conversation.save_reply(self.task,-42,result,'unused')
        rows=tasks.get_response_short_memory(chat_id=-42,before_message_id=3)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['user_id'],999);self.assertEqual(rows[0]['role'],'assistant')
        self.assertEqual(tasks.get_response_short_memory(chat_id=-43,before_message_id=3),[])
        with closing(tasks.get_connection()) as c,c:c.execute("UPDATE ai_tasks SET task_type='mechanics' WHERE id=?",(self.task,))
        conversation.save_reply(self.task,-42,dict(result,message_id=4),'technical')
        with closing(tasks.get_connection()) as c:self.assertEqual(c.execute('SELECT count(*) FROM messages_reactions').fetchone()[0],1)
    def message(self):
        reply=Obj(message_id=10,chat=Obj(id=-42),from_user=Obj(id=999,full_name='Bot',is_bot=True,username='bot'),text='Полный исходный ответ',caption=None,photo=[Obj(file_id='reference')])
        return Obj(message_id=11,chat=Obj(id=-42,type='supergroup'),from_user=Obj(id=1,is_bot=False),text='Бот, перерисуй фото',caption=None,photo=[],reply_to_message=reply,quote=Obj(text='исходный'))
    def test_quote_and_reply_photo_preserve_source(self):
        message=self.message();text=conversation.request_text(message,999)
        self.assertIn('твой собственный ответ',text);self.assertIn('Цитируемая часть: исходный',text)
        ref=conversation.reply_image(message,999)
        self.assertEqual(ref.photo[0].file_id,'reference');self.assertEqual(ref.message_id,11)
        message.reply_to_message.chat.id=-43
        self.assertIsNone(conversation.reply_image(message,999));self.assertEqual(conversation.request_text(message,999),message.text)
    def test_own_role_is_explicit_in_prompt(self):
        prompt=tasks.build_response_prompt(chat_id=-42,request_message_id=3,requester_user_id=1,requester_name='User',requester_nick=None,message_text='Почему?',trigger_reason='reply_to_bot',short_memory=[dict(date='2026-10-05',message_id=2,text='Ответ',role='assistant')],long_memory=[],profile_json=None)
        self.assertIn('assistant: твой собственный ответ',prompt)
    def test_reply_photo_reaches_existing_image_payload(self):
        import json,imagegen
        message=self.message();message.text='Убери фон'
        reference=conversation.reply_image(message,999)
        self.assertTrue(imagegen.addressed(reference,'bot'))
        imagegen.collect(reference,'bot')
        with closing(tasks.get_connection()) as c,c:c.execute("UPDATE ai_imagegen_batches SET status='typed'")
        ident=imagegen.create_task(chat_id=-42,user_id=1,request_message_id=11,user_query=message.text)
        with closing(tasks.get_connection()) as c:
            payload=json.loads(c.execute('SELECT payload_json FROM ai_tasks WHERE id=?',(ident,)).fetchone()[0])
        self.assertEqual(payload['photos'][0]['file_id'],'reference')

    def test_reply_and_new_album_keep_order_and_confirmation(self):
        import json,imagegen
        message=self.message();message.text=None;message.caption='Добавь девушку в этот кадр'
        message.media_group_id='album';message.photo=[Obj(file_id='new-photo')]
        imagegen.collect(conversation.reply_image(message,999),'bot')
        imagegen.collect(conversation.reply_image(message,999),'bot')
        with closing(tasks.get_connection()) as c:
            batch=dict(c.execute('SELECT * FROM ai_imagegen_batches').fetchone())
            self.assertEqual([p['file_id'] for p in imagegen.photos(c,batch['id'])],['reference','new-photo'])
        for n in (14,13,12):
            message.message_id=n;message.photo=[Obj(file_id=f'new-{n}')]
            imagegen.collect(conversation.reply_image(message,999),'bot')
        with closing(tasks.get_connection()) as c:
            refs=imagegen.photos(c,batch['id'])
        self.assertEqual(len(refs),5)
        import asyncio,imagegen_bot
        from unittest.mock import AsyncMock
        bot=Obj(send_message=AsyncMock(return_value=Obj(message_id=777)))
        with closing(tasks.get_connection()) as c,c:c.execute('UPDATE ai_imagegen_batches SET touched_at=0')
        with patch.object(imagegen_bot.asyncio,'sleep',side_effect=asyncio.CancelledError),patch.object(imagegen,'queue_type') as typed:
            with self.assertRaises(asyncio.CancelledError):asyncio.run(imagegen_bot.collect_loop(bot))
            typed.assert_not_called()
        bot.send_message.assert_awaited_once()
        imagegen.choose(batch['id'],1,-42,True)
        self.assertEqual([p['message_id'] for p in refs],[10,11,12,13,14])
        # A restart/migration keeps the same reference, without duplicating it.
        tasks.ensure_ai_tables()
        with closing(tasks.get_connection()) as c,c:c.execute("UPDATE ai_imagegen_batches SET status='typed'")
        ident=imagegen.create_task(chat_id=-42,user_id=1,request_message_id=11,user_query=message.caption)
        with closing(tasks.get_connection()) as c:
            payload=json.loads(c.execute('SELECT payload_json FROM ai_tasks WHERE id=?',(ident,)).fetchone()[0])
        self.assertEqual([p['file_id'] for p in payload['photos']],['reference','new-photo','new-12','new-13'])
