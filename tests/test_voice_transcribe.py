import asyncio
import io
import json
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from aiogram.types import Message
import db
import voice_transcribe as voice
import ai_runtime


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(db,'DB_FILE',str(Path(self.tmp.name)/'test.db')); self.db.start()
        with closing(db.get_connection()) as conn, conn:
            voice.ensure_schema(conn); voice.ensure_schema(conn)
            conn.execute('CREATE TABLE messages_reactions(chat_id INTEGER,message_id INTEGER,user_id INTEGER,message_text TEXT)')
            conn.execute("INSERT INTO messages_reactions VALUES(-42,10,1,'')")
        self.bot = SimpleNamespace(id=999,send_message=AsyncMock(),download=AsyncMock())
        self.pipeline = AsyncMock()

    def tearDown(self):
        self.db.stop(); self.tmp.cleanup()

    def message(self, reply=None):
        return Message.model_validate({'message_id':10,'date':datetime.now(timezone.utc),
            'chat':{'id':-42,'type':'supergroup'},'from':{'id':1,'is_bot':False,'first_name':'User'},
            'voice':{'file_id':'voice','file_unique_id':'unique','duration':12},
            **({'reply_to_message':{'message_id':9,'date':datetime.now(timezone.utc),
                'chat':{'id':-42,'type':'supergroup'},'from':{'id':reply,'is_bot':True,'first_name':'Bot'},
                'text':'Предыдущий ответ'}} if reply else {})})

    def ready(self,text,message=None):
        message = message or self.message()
        voice.enqueue(message)
        row,_ = voice.claim()
        voice.save_text(row,text)
        row['transcript'] = text; row['status'] = 'ready'
        return row

    def test_prefix_is_only_first_word(self):
        for text in ['Бот, расскажи','  БОТ расскажи','бот: расскажи',
                     'Вот, расскажи','  ВОТ расскажи','вот: расскажи']:
            reason,clean = voice.addressed(self.message(),text,999)
            self.assertEqual('mention_bot_word',reason); self.assertEqual('расскажи',clean)
        for text in ['ботаника','Мне сказал бот','работа бота','вотчина','Мне вот сказали']:
            self.assertIsNone(voice.addressed(self.message(),text,999)[0])

    def test_reply_is_scoped_to_own_bot(self):
        self.assertEqual('reply_to_bot',voice.addressed(self.message(999),'Почему?',999)[0])
        self.assertIsNone(voice.addressed(self.message(888),'Почему?',999)[0])

    def test_cepen_and_other_bot_voices_never_enter_queue(self):
        from aiogram.types import User
        for bot_id in (999,888):
            message = self.message().model_copy(update={
                'from_user':User(id=bot_id,is_bot=True,first_name='Cepen')})
            self.assertFalse(voice.enqueue(message))
        self.assertIsNone(voice.claim()[0])

    def test_ordinary_transcript_no_typing_and_plain_text(self):
        row = self.ready('<b>встречаемся вечером</b>')
        asyncio.run(voice.deliver(self.bot,self.message(),row,self.pipeline))
        self.pipeline.assert_not_awaited()
        self.assertEqual('<b>встречаемся вечером</b>',self.bot.send_message.await_args.args[1])
        self.assertIsNone(self.bot.send_message.await_args.kwargs['parse_mode'])

    def test_addressed_uses_original_id_and_reply_context(self):
        msg = self.message(999); row = self.ready('Бот, покажи баланс',msg)
        with patch('ai_runtime.enabled',return_value=True):
            asyncio.run(voice.deliver(self.bot,msg,row,self.pipeline))
        derived = self.pipeline.await_args.args[0]
        self.assertEqual('покажи баланс',derived.text)
        self.assertEqual(10,derived.message_id); self.assertEqual(999,derived.reply_to_message.from_user.id)
        self.bot.send_message.assert_not_awaited()

    def test_disabled_ai_still_transcribes(self):
        row = self.ready('Бот, привет')
        with patch('ai_runtime.enabled',return_value=False):
            asyncio.run(voice.deliver(self.bot,self.message(),row,self.pipeline))
        self.pipeline.assert_not_awaited(); self.bot.send_message.assert_awaited_once()

    def test_repeated_update_and_saved_source(self):
        self.assertTrue(voice.enqueue(self.message())); self.assertFalse(voice.enqueue(self.message()))
        row,_ = voice.claim(); voice.save_text(row,'Текст из голоса')
        with closing(db.get_connection()) as conn:
            self.assertEqual('Текст из голоса',conn.execute('SELECT message_text FROM messages_reactions').fetchone()[0])
            self.assertEqual(1,conn.execute('SELECT count(*) FROM messages_reactions').fetchone()[0])
        row,_ = voice.claim(); self.assertEqual('ready',row['status'])

    def test_ambiguous_delivery_is_not_retried(self):
        row = self.ready('Привет')
        self.bot.send_message.side_effect = TimeoutError()
        asyncio.run(voice.process(self.bot,row,self.pipeline))
        self.assertIsNone(voice.claim()[0]); self.bot.send_message.assert_awaited_once()

    def test_long_transcript_is_split(self):
        row = self.ready('а'*9000)
        asyncio.run(voice.deliver(self.bot,self.message(),row,self.pipeline))
        self.assertEqual(3,self.bot.send_message.await_count)

    def test_extract_and_quota(self):
        self.assertEqual('Бот, привет',voice.extract({'status':'completed','steps':[
            {'type':'model_output','content':[{'type':'text','text':'Бот, привет'}]}]}))
        self.assertEqual(25,ai_runtime.model_limits(voice.MODEL)['RPD'])

    def test_voice_metadata_is_not_accessible_to_sql(self):
        import ai_tasks
        with self.assertRaises(ValueError):
            ai_tasks.validate_text_to_sql('SELECT * FROM ai_voice_transcripts',chat_id=-42)

    def test_main_enqueues_voice_even_with_caption(self):
        import ast
        from unittest.mock import Mock
        tree = ast.parse((Path(__file__).resolve().parents[1]/'main.py').read_text(encoding='utf-8-sig'))
        function = next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='handle_message')
        function.decorator_list = []
        typed = AsyncMock()
        scope = {'types':SimpleNamespace(Message=Message),'asyncio':asyncio,
                 'update_stats':Mock(),'maybe_create_ai_response_task':typed,
                 'handle_mujlo_message':AsyncMock()}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'main.py','exec'),scope)
        message = self.message().model_copy(update={'caption':'Подпись'})
        asyncio.run(scope['handle_message'](message))
        typed.assert_not_awaited(); self.assertIsNotNone(voice.claim()[0])

    def test_restart_preserves_ready_and_fences_unknown_delivery(self):
        voice.enqueue(self.message())
        with closing(db.get_connection()) as conn,conn:
            for index,state in enumerate(('processing','delivering','ready'),start=20):
                conn.execute('INSERT INTO ai_voice_transcripts(chat_id,message_id,source_json,status,transcript,created_at,updated_at) VALUES(-42,?,?,?,?,?,?)',
                             (index,'{}',state,'saved','now','now'))
        with patch('voice_transcribe.claim',side_effect=asyncio.CancelledError()):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(voice.worker(self.bot,self.pipeline))
        with closing(db.get_connection()) as conn:
            states = [(r['status'],r['transcript']) for r in conn.execute('SELECT * FROM ai_voice_transcripts WHERE message_id>=20 ORDER BY message_id')]
        self.assertEqual([('queued','saved'),('failed','saved'),('ready','saved')],states)

    def test_minute_quota_defers_and_daily_reports(self):
        from ai_providers import ProviderUnavailable
        voice.enqueue(self.message()); row,_ = voice.claim()
        for kind in ('minute','daily'):
            with patch('voice_transcribe.transcribe',side_effect=ProviderUnavailable('quota',60,False,kind)):
                asyncio.run(voice.process(self.bot,row,self.pipeline))
            with closing(db.get_connection()) as conn:
                state = conn.execute('SELECT status FROM ai_voice_transcripts').fetchone()[0]
            self.assertEqual('queued' if kind=='minute' else 'failed',state)
        self.assertIn('Суточный лимит',self.bot.send_message.await_args.args[1])
