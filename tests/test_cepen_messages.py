import asyncio
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiogram.enums import ChatMemberStatus

import db
import cepen_messages as letters
import test_cepen

CHAT = test_cepen.CHAT


class LetterTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_cepen.CepenTests()
        self.fixture.setUp()
        self.now = datetime(2026,10,4,12,tzinfo=letters.TZ)
        with closing(db.get_connection()) as conn:
            conn.execute('UPDATE users SET cepen=5 WHERE user_id=1')
            letters.ensure_schema(conn)
            # These legacy delivery tests exercise the exhausted-TTS text path.
            conn.execute('INSERT INTO cepen_letters(chat_id,user_id,due_at,tts_attempt) VALUES (?,?,?,3)', (CHAT,1,letters.stamp(self.now)))
            conn.commit()

    def tearDown(self):
        self.fixture.tearDown()

    def test_interval_and_quiet_fallback(self):
        for hour in range(24):
            now = self.now.replace(hour=hour)
            due = letters.next_time(now)
            self.assertTrue(10 <= due.hour < 23)
            delay = (due-now).total_seconds()/3600
            self.assertTrue(68 <= delay <= 74 or 76 <= delay <= 90)
        with patch('cepen_messages.random.randrange',return_value=0):
            due = letters.next_time(self.now.replace(hour=4))
            self.assertEqual(84,(due-self.now.replace(hour=4)).total_seconds()/3600)

    def test_repeat_schema_and_schedule(self):
        letters.schedule(self.now)
        letters.schedule(self.now)
        with closing(db.get_connection()) as conn:
            self.assertEqual(1,conn.execute('SELECT COUNT(*) FROM cepen_letters').fetchone()[0])

    def test_absent_owner_no_generation(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = SimpleNamespace(status='left')
        with patch('ai_runtime.mode',return_value='api'),patch('cepen_messages.generate') as generate:
            self.assertEqual(0,asyncio.run(letters.dispatch(bot,self.now)))
            generate.assert_not_called()
        bot.send_message.assert_not_called()

    def test_delivery_and_gift_once(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = SimpleNamespace(status=ChatMemberStatus.MEMBER)
        with patch('ai_runtime.mode',return_value='api'),patch('cepen_messages.context',return_value={'mood':'счастлив'}),patch('cepen_messages.generate',return_value='Нашёл {gift} сита под ковром!'),patch('cepen_messages.random.randint',return_value=133):
            self.assertEqual(1,asyncio.run(letters.dispatch(bot,self.now)))
            self.assertEqual(0,asyncio.run(letters.dispatch(bot,self.now)))
        bot.send_message.assert_awaited_once()
        self.assertIn('1.33',bot.send_message.await_args.args[1])
        with closing(db.get_connection()) as conn:
            self.assertEqual(1,conn.execute("SELECT COUNT(*) FROM sit_ledger WHERE action_code='cepen_letter_gift'").fetchone()[0])

    def test_unknown_delivery_never_retries_same_gift(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = SimpleNamespace(status='member')
        bot.send_message.side_effect = TimeoutError('unknown')
        with patch('ai_runtime.mode',return_value='api'),patch('cepen_messages.context',return_value={'mood':'счастлив'}),patch('cepen_messages.generate',return_value='Дарю {gift} сита!'):
            asyncio.run(letters.dispatch(bot,self.now))
            asyncio.run(letters.dispatch(bot,self.now+timedelta(hours=8)))
        bot.send_message.assert_awaited_once()
        with closing(db.get_connection()) as conn:
            self.assertEqual(1,conn.execute("SELECT COUNT(*) FROM sit_ledger WHERE action_code='cepen_letter_gift'").fetchone()[0])

    def test_generation_failure_no_gift(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = SimpleNamespace(status='member')
        with patch('ai_runtime.mode',return_value='api'),patch('cepen_messages.context',return_value={'mood':'счастлив'}),patch('cepen_messages.generate',side_effect=TimeoutError()):
            asyncio.run(letters.dispatch(bot,self.now))
        bot.send_message.assert_not_called()
        with closing(db.get_connection()) as conn:
            self.assertEqual(0,conn.execute("SELECT COUNT(*) FROM sit_ledger WHERE action_code='cepen_letter_gift'").fetchone()[0])

    def test_context_is_scoped_and_mood_uses_today(self):
        with closing(db.get_connection()) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS messages_reactions(chat_id INTEGER,message_id INTEGER,user_id INTEGER,message_text TEXT,date TEXT)')
            for index in range(55):
                conn.execute('INSERT INTO messages_reactions(chat_id,message_id,user_id,message_text,date) VALUES (?,?,?,?,?)', (CHAT,index,1,str(index),'2026-10-04'))
            conn.execute('INSERT INTO messages_reactions(chat_id,message_id,user_id,message_text,date) VALUES (?,?,?,?,?)', (CHAT,99,2,' чужие данные ','2026-10-04'))
            conn.commit()
        data = letters.context(CHAT,1,self.now)
        self.assertEqual(50,len(data['recent_messages']))
        self.assertEqual('54',data['recent_messages'][0])
        self.assertNotIn(' чужие данные ',data['recent_messages'])
        self.assertEqual('подавлен',data['mood'])
        with closing(db.get_connection()) as conn:
            for index in range(10):
                conn.execute('INSERT INTO cepen_scratches VALUES (?,?,?,?,?,?,?)', (str(index),'2026-10-04',CHAT,1,2,0.1,'2026-10-04'))
            conn.commit()
        self.assertEqual('счастлив',letters.context(CHAT,1,self.now)['mood'])
