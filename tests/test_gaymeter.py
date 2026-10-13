import asyncio
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Dispatcher

import db
import ai_tasks
import gaymeter
import sosalsa


CHAT = -500


class GaymeterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_FILE
        db.DB_FILE = str(Path(self.tmp.name) / 'stats.db')
        with closing(db.get_connection()) as conn:
            conn.executescript('''
                CREATE TABLE users(user_id INTEGER,chat_id INTEGER,name TEXT,nick TEXT,
                    sex TEXT,sits REAL DEFAULT 10,PRIMARY KEY(user_id,chat_id));
                CREATE TABLE sosalsa_stats(chat_id INTEGER,user_id1 INTEGER,user_id2 INTEGER,
                    sosalsa_count INTEGER DEFAULT 0,shpehalsa_count INTEGER DEFAULT 0,
                    PRIMARY KEY(chat_id,user_id1,user_id2));
                CREATE TABLE daily_stats(user_id INTEGER,chat_id INTEGER,date TEXT,
                    PRIMARY KEY(user_id,chat_id,date));
                CREATE TABLE total_stats(user_id INTEGER,chat_id INTEGER,
                    PRIMARY KEY(user_id,chat_id));
                CREATE TABLE body_parts(id INTEGER PRIMARY KEY,name_acc TEXT);
                CREATE TABLE user_body_parts(id INTEGER PRIMARY KEY,user_id INTEGER,
                    chat_id INTEGER,body_part_id INTEGER,state INTEGER,
                    UNIQUE(user_id,chat_id,body_part_id));
                INSERT INTO body_parts VALUES(1,'щеку');
            ''')
            conn.executemany('INSERT INTO users(user_id,chat_id,name,sex) VALUES(?,?,?,?)',
                             [(1,CHAT,'<Первый>','m'),(2,CHAT,'Вторая','f'),
                              (3,CHAT,'Третий','m'),(4,CHAT,'Четвёртый',None),
                              (1,-600,'Первый','m'),(2,-600,'Вторая','m')])
            conn.commit()
        db.initialize_db()

    def tearDown(self):
        db.DB_FILE = self.old_db
        self.tmp.cleanup()

    def test_old_schema_migrates_twice_without_changing_totals(self):
        with closing(db.get_connection()) as conn, conn:
            conn.execute('INSERT INTO total_stats(user_id,chat_id,bites_given) VALUES(1,?,17)',(CHAT,))
            gaymeter.log_bite(conn,CHAT,1,2,1)
        db.initialize_db()
        with closing(db.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM bite_events').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT bites_given FROM total_stats').fetchone()[0],17)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name LIKE 'idx_bite_events_%'").fetchone()[0],2)

    def test_weights_directions_chat_isolation_and_current_sex(self):
        with closing(db.get_connection()) as conn, conn:
            conn.executemany('INSERT INTO sosalsa_stats VALUES(?,?,?,?,?)',
                             [(CHAT,1,2,2,1),(CHAT,1,3,1,2),(-600,1,2,100,100)])
            gaymeter.log_bite(conn,CHAT,1,2,1)
            gaymeter.log_bite(conn,CHAT,2,1,1)
            gaymeter.log_bite(conn,CHAT,1,3,1)
            gaymeter.log_bite(conn,-600,1,2,1)
        stats = gaymeter.get_stats(CHAT,1)
        self.assertEqual(stats['total'],42)
        self.assertEqual(stats['same'],24)
        self.assertAlmostEqual(stats['percent'],100*24/42)
        with closing(db.get_connection()) as conn, conn:
            conn.execute('UPDATE users SET sex="m" WHERE user_id=2 AND chat_id=?',(CHAT,))
        self.assertEqual(gaymeter.get_stats(CHAT,1)['percent'],100)
        with closing(db.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT victim_sex FROM bite_events WHERE id=1').fetchone()[0],'f')

    def test_unknown_partner_is_only_in_denominator_and_legacy_bites_excluded(self):
        with closing(db.get_connection()) as conn, conn:
            conn.execute('INSERT INTO sosalsa_stats VALUES(?,1,2,1,0)',(CHAT,))
            conn.execute('INSERT INTO sosalsa_stats VALUES(?,1,4,1,0)',(CHAT,))
            conn.execute('INSERT INTO sosalsa_stats VALUES(?,1,99,1,0)',(CHAT,))
            conn.execute('INSERT INTO total_stats(user_id,chat_id,bites_given,bites_received) VALUES(1,?,100,100)',(CHAT,))
        stats=gaymeter.get_stats(CHAT,1)
        self.assertAlmostEqual(stats['percent'],0)
        self.assertEqual(stats['unknown'],2)
        self.assertIn('неизвестным полом',gaymeter.format_result('Имя',stats))

    def test_no_events_and_unknown_author_do_not_show_zero(self):
        self.assertIsNone(gaymeter.get_stats(CHAT,1)['percent'])
        with closing(db.get_connection()) as conn, conn:
            gaymeter.log_bite(conn,CHAT,1,4,1)
        self.assertIsNone(gaymeter.get_stats(CHAT,4)['percent'])
        self.assertIn('не указан',gaymeter.format_result('Имя',gaymeter.get_stats(CHAT,4)))

    def test_phrases_and_bar_endpoints(self):
        self.assertEqual(len(set(gaymeter.PHRASES)),30)
        self.assertTrue(all(len(s.split())<=7 for s in gaymeter.PHRASES))
        for percent,segments in ((0,0),(100,10),(49.9,5)):
            text=gaymeter.format_result('<Имя>',{'percent':percent,'sex':'m','unknown':0})
            self.assertEqual(text.count('🍆'),segments)
            self.assertIn('<Имя>',text)

    def test_sql_history_requires_current_chat_filter(self):
        with self.assertRaises(ai_tasks.TextToSqlError):
            ai_tasks.validate_text_to_sql('SELECT * FROM bite_events',chat_id=CHAT)
        with self.assertRaises(ai_tasks.TextToSqlError):
            ai_tasks.validate_text_to_sql('SELECT * FROM bite_events WHERE chat_id=-600',chat_id=CHAT)
        self.assertIn('bite_events',ai_tasks.validate_text_to_sql('SELECT * FROM bite_events WHERE chat_id=-500',chat_id=CHAT))

    def test_reply_registration_and_private_chat(self):
        dp=Dispatcher();sosalsa.register_sos_handlers(dp)
        self.assertIn(gaymeter.gay_command,[h.callback for h in dp.message.handlers])
        message=SimpleNamespace(chat=SimpleNamespace(id=CHAT,type='supergroup'),
                                from_user=SimpleNamespace(id=1,full_name='Имя'),
                                sender_chat=None,reply=AsyncMock())
        asyncio.run(gaymeter.gay_command(message))
        message.reply.assert_awaited_once()
        self.assertIn('Первый',message.reply.call_args.args[0])
        self.assertEqual(message.reply.call_args.kwargs['parse_mode'],None)
        message.chat.type='private';message.reply.reset_mock()
        asyncio.run(gaymeter.gay_command(message))
        self.assertIn('групповом',message.reply.call_args.args[0])
        message.chat.type='supergroup';message.sender_chat=object();message.reply.reset_mock()
        asyncio.run(gaymeter.gay_command(message))
        self.assertIn('аккаунта',message.reply.call_args.args[0])

    def run_bite(self):
        dp=Dispatcher();sosalsa.register_sos_handlers(dp)
        query=SimpleNamespace(data='random_bite',from_user=SimpleNamespace(id=1),
            message=SimpleNamespace(chat=SimpleNamespace(id=CHAT),answer=AsyncMock()),answer=AsyncMock())
        with patch('sosalsa.get_active_users',return_value=[2]), \
             patch('sosalsa.get_sits',return_value=10), \
             patch('sosalsa.try_bite_dick',return_value=None), \
             patch('sosalsa.cepen.attempt_pair',return_value=None):
            asyncio.run(dp.callback_query.handlers[0].callback(query))
        return query

    def test_real_bite_callback_logs_with_balance_and_counters(self):
        self.run_bite()
        with closing(db.get_connection()) as conn:
            row=conn.execute('SELECT * FROM bite_events').fetchone()
            self.assertEqual((row['biter_id'],row['victim_id'],row['biter_sex'],row['victim_sex']),(1,2,'m','f'))
            ledger=conn.execute('SELECT * FROM sit_ledger').fetchone()
            self.assertEqual(json.loads(ledger['metadata_json']),{'target_user_id':2,'body_part_id':1})
            self.assertEqual(ledger['amount'],-1)
            self.assertEqual(conn.execute('SELECT sits FROM users WHERE user_id=1 AND chat_id=?',(CHAT,)).fetchone()[0],9)
            self.assertEqual(conn.execute('SELECT state FROM user_body_parts WHERE user_id=2').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT bites_given FROM total_stats WHERE user_id=1').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT bites_received FROM daily_stats WHERE user_id=2').fetchone()[0],1)

    def test_failed_log_rolls_back_balance_body_and_counters(self):
        original=gaymeter.log_bite
        def fail(*args):
            original(*args)
            raise RuntimeError('simulated failure after insert')
        with patch('gaymeter.log_bite',side_effect=fail):
            with self.assertRaises(RuntimeError):self.run_bite()
        with closing(db.get_connection()) as conn:
            for table in ('bite_events','sit_ledger','daily_stats','total_stats'):
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT sits FROM users WHERE user_id=1 AND chat_id=?',(CHAT,)).fetchone()[0],10)
            self.assertEqual(conn.execute('SELECT state FROM user_body_parts WHERE user_id=2').fetchone()[0],1)

    def test_balance_race_rejects_bite_without_writes(self):
        with closing(db.get_connection()) as conn, conn:
            conn.execute('UPDATE users SET sits=0 WHERE user_id=1 AND chat_id=?',(CHAT,))
        query=self.run_bite()
        self.assertIn('Недостаточно',query.answer.call_args.args[0])
        with closing(db.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM bite_events').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM sit_ledger').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT state FROM user_body_parts WHERE user_id=2').fetchone()[0],1)


if __name__ == '__main__':
    unittest.main()
