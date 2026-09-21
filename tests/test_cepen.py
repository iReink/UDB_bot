import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import cepen
import db
import settings


CHAT = -500


class CepenTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_file = db.DB_FILE
        self.old_settings_db_file = settings.DB_PATH
        db.DB_FILE = str(Path(self.temp_dir.name) / "stats.db")
        settings.DB_PATH = db.DB_FILE
        with closing(sqlite3.connect(db.DB_FILE)) as conn:
            conn.executescript("""
                CREATE TABLE users (
                    user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                    name TEXT, nick TEXT, sits REAL DEFAULT 0,
                    subscription_till TEXT,
                    PRIMARY KEY (user_id,chat_id)
                );
                CREATE TABLE daily_stats (
                    user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, date TEXT NOT NULL,
                    messages INTEGER DEFAULT 0, stickers INTEGER DEFAULT 0, coffee INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id,chat_id,date)
                );
                CREATE TABLE total_stats (
                    user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                    messages INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id,chat_id)
                );
                CREATE TABLE daily_events (
                    id INTEGER PRIMARY KEY, chat_id INTEGER, date TEXT, time TEXT
                );
                CREATE TABLE daily_participants (daily_id INTEGER, user_id INTEGER);
                CREATE TABLE dicks (
                    user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                    length INTEGER DEFAULT 0, grow_date TEXT DEFAULT '',
                    buff TEXT DEFAULT '', buff_exp TEXT DEFAULT '',
                    top1_entrance_date TEXT DEFAULT '',
                    PRIMARY KEY (user_id,chat_id)
                );
                CREATE TABLE settings (
                    chat_id INTEGER NOT NULL, name TEXT NOT NULL, value TEXT,
                    PRIMARY KEY (chat_id,name)
                );
            """)
            conn.executemany(
                "INSERT INTO users(user_id,chat_id,name,nick,sits) VALUES (?,?,?,?,?)",
                [(1, CHAT, "Первый", "first", 10), (2, CHAT, "Второй", "second", 60),
                 (3, CHAT, "Третий", "third", 0)],
            )
            conn.commit()
        db.initialize_db()

    def tearDown(self):
        db.DB_FILE = self.old_db_file
        settings.DB_PATH = self.old_settings_db_file
        self.temp_dir.cleanup()

    def test_migration_repeats_and_display_prefixes(self):
        db.initialize_db()
        with closing(db.get_connection()) as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
            conn.execute("UPDATE users SET cepen=5, subscription_till='2999-01-01' WHERE user_id=1")
            conn.commit()
        self.assertIn("cepen", columns)
        self.assertIn("cepen_growth_date", columns)
        self.assertEqual("👑 🪱 Первый", db.get_user_display_name(1, CHAT))

    def test_primary_probability_and_repeat(self):
        with patch("cepen.random.random", return_value=.004):
            self.assertIsNone(cepen.attempt_primary(CHAT, 1, "coffee"))
        with patch("cepen.random.random", return_value=.003):
            text = cepen.attempt_primary(CHAT, 1, "coffee")
        self.assertIn("@first", text)
        self.assertIn(cepen.INSTRUCTION, text)
        self.assertEqual(5, cepen.length(CHAT, 1))
        with patch("cepen.random.random", return_value=0):
            self.assertIn("tg://user?id=99", cepen.attempt_primary(CHAT, 99, "sticker"))
        self.assertEqual(5, cepen.length(CHAT, 99))
        with patch("cepen.random.random", return_value=0):
            self.assertIsNone(cepen.attempt_primary(CHAT, 1, "sticker"))
        self.assertEqual(5, cepen.length(CHAT, 1))

    def test_directional_pair_probabilities(self):
        with patch("cepen.random.random", return_value=0):
            cepen.attempt_primary(CHAT, 2, "coffee")
        with patch("cepen.random.random", return_value=.5):
            text = cepen.attempt_pair(CHAT, 1, 2, "sos")
        self.assertIn("@first засосал @second", text)
        with patch("cepen.random.random", return_value=.5):
            self.assertIsNone(cepen.attempt_pair(CHAT, 2, 3, "sos"))
        with patch("cepen.random.random", return_value=.05):
            text = cepen.attempt_pair(CHAT, 2, 3, "sos")
        self.assertIn("@second всосал в @third", text)

    def test_group_event_is_once_and_uses_original_carrier(self):
        with patch("cepen.random.random", return_value=0):
            cepen.attempt_primary(CHAT, 1, "geyser")
            notices = cepen.attempt_event(CHAT, "group", "event-1", [1, 2], [3])
        self.assertEqual(2, len(notices))
        self.assertTrue(all("@first" in text for text in notices))
        self.assertEqual([], cepen.attempt_event(CHAT, "group", "event-1", [1, 2], [3]))

    def test_cure_purchase_is_atomic_and_reinfection_starts_at_five(self):
        with patch("cepen.random.random", return_value=0):
            cepen.attempt_primary(CHAT, 1, "coffee")
            cepen.attempt_primary(CHAT, 2, "coffee")
        self.assertEqual("insufficient", cepen.cure(CHAT, 1, price=50))
        self.assertEqual(5, cepen.length(CHAT, 1))
        self.assertEqual("cured", cepen.cure(CHAT, 2, price=50))
        self.assertEqual("healthy", cepen.cure(CHAT, 2, price=50))
        with closing(db.get_connection()) as conn:
            balance = conn.execute("SELECT sits FROM users WHERE user_id=2").fetchone()[0]
            ledger = conn.execute("SELECT amount,action_code FROM sit_ledger").fetchall()
        self.assertEqual(10, balance)
        self.assertEqual([(-50, "cepen_cure_purchase")], [tuple(row) for row in ledger])
        with patch("cepen.random.random", return_value=0):
            cepen.attempt_primary(CHAT, 2, "coffee")
        self.assertEqual(5, cepen.length(CHAT, 2))

    def test_growth_charge_and_anabiosis_are_once(self):
        today = "2026-09-21"
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET cepen=5,sits=10 WHERE user_id=1")
            conn.execute("UPDATE users SET cepen=5,sits=0 WHERE user_id=3")
            conn.execute("INSERT INTO daily_stats(user_id,chat_id,date,messages) VALUES (3,?,?,5)", (CHAT, today))
            conn.execute("INSERT INTO total_stats(user_id,chat_id,messages) VALUES (3,?,12)", (CHAT,))
            conn.commit()
        report = cepen.grow_all(today)[CHAT]
        self.assertEqual(2, len(report))
        self.assertTrue(any("половину сообщений" in line for line in report))
        self.assertEqual({}, cepen.grow_all(today))
        with closing(db.get_connection()) as conn:
            first = conn.execute("SELECT cepen,sits FROM users WHERE user_id=1").fetchone()
            third = conn.execute("SELECT cepen FROM users WHERE user_id=3").fetchone()
            daily = conn.execute("SELECT messages FROM daily_stats WHERE user_id=3").fetchone()[0]
            total = conn.execute("SELECT messages FROM total_stats WHERE user_id=3").fetchone()[0]
            ledger = conn.execute("SELECT amount FROM sit_ledger WHERE action_code='cepen_growth'").fetchall()
        self.assertEqual((5.75, 9.5), tuple(first))
        self.assertEqual(5, third[0])
        self.assertEqual((2, 9), (daily, total))
        self.assertEqual([-.5], [row[0] for row in ledger])
        self.assertEqual((6.613, .863, .575), cepen.growth_preview(5.75))
        self.assertEqual(20, len(cepen.GROWTH_LINES))

    def test_full_growth_can_increase_dick_by_integer_cost(self):
        today = "2026-09-21"
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET cepen=100,sits=10 WHERE user_id=1")
            conn.execute("INSERT INTO dicks(user_id,chat_id,length) VALUES (1,?,90)", (CHAT,))
            conn.commit()

        report = cepen.grow_all(today)[CHAT][0]
        with closing(db.get_connection()) as conn:
            user = conn.execute("SELECT cepen,sits FROM users WHERE user_id=1").fetchone()
            dick_row = conn.execute(
                "SELECT length,top1_entrance_date FROM dicks WHERE user_id=1"
            ).fetchone()
            ledger = conn.execute(
                "SELECT amount,metadata_json FROM sit_ledger WHERE action_code='cepen_growth'"
            ).fetchone()
        self.assertEqual((115, 0), tuple(user))
        self.assertEqual((100, today), tuple(dick_row))
        self.assertIn("вырос на 10 см", report)
        self.assertEqual(-10, ledger[0])
        self.assertIn('"mode": "full"', ledger[1])

    def test_partial_growth_spends_balance_without_dick_bonus(self):
        today = "2026-09-21"
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET cepen=100,sits=5 WHERE user_id=2")
            conn.execute("INSERT INTO dicks(user_id,chat_id,length) VALUES (2,?,90)", (CHAT,))
            conn.commit()

        preview = cepen.status_text(CHAT, 2)
        self.assertIn("частичный рост до 107.5 см", preview)
        self.assertIn("баланс сита был больше на 5", preview)
        report = cepen.grow_all(today)[CHAT][0]
        with closing(db.get_connection()) as conn:
            user = conn.execute("SELECT cepen,sits FROM users WHERE user_id=2").fetchone()
            dick_length = conn.execute("SELECT length FROM dicks WHERE user_id=2").fetchone()[0]
        self.assertEqual((107.5, 0), tuple(user))
        self.assertEqual(90, dick_length)
        self.assertIn("50% полного роста", report)
        self.assertIn("На рост члена сил не осталось", report)

    def test_growth_preview_explains_anabiosis_and_full_dick_bonus(self):
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET cepen=100,sits=.5 WHERE user_id=1")
            conn.execute("INSERT INTO dicks(user_id,chat_id,length) VALUES (1,?,90)", (CHAT,))
            conn.commit()
        text = cepen.status_text(CHAT, 1)
        self.assertIn("анабиоз", text)
        self.assertIn("минимум 1 сит", text)
        self.assertIn("баланс сита был больше на 9.5", text)

        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET sits=10 WHERE user_id=1")
            conn.commit()
        text = cepen.status_text(CHAT, 1)
        self.assertIn("полный рост до 115 см", text)
        self.assertIn("Член вырастет на 10 см", text)

    def test_partial_cure_is_atomic_repeatable_and_has_floor(self):
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET cepen=100,sits=25 WHERE user_id=2")
            conn.commit()
        self.assertEqual(("reduced", 100, 80), cepen.partial_cure(CHAT, 2))
        self.assertEqual(("reduced", 80, 64), cepen.partial_cure(CHAT, 2))
        with closing(db.get_connection()) as conn:
            row = conn.execute("SELECT cepen,sits FROM users WHERE user_id=2").fetchone()
            ledger = conn.execute(
                "SELECT amount,action_code FROM sit_ledger ORDER BY id"
            ).fetchall()
            conn.execute("UPDATE users SET cepen=5 WHERE user_id=2")
            conn.commit()
        self.assertEqual((64, 5), tuple(row))
        self.assertEqual(
            [(-10, "cepen_partial_cure"), (-10, "cepen_partial_cure")],
            [tuple(item) for item in ledger],
        )
        self.assertEqual(("minimum", 5, 5), cepen.partial_cure(CHAT, 2))

    def test_partial_cure_does_not_charge_when_insufficient(self):
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET cepen=100,sits=9 WHERE user_id=1")
            conn.commit()
        self.assertEqual(("insufficient", 100, 100), cepen.partial_cure(CHAT, 1))
        with closing(db.get_connection()) as conn:
            row = conn.execute("SELECT cepen,sits FROM users WHERE user_id=1").fetchone()
            ledger_count = conn.execute("SELECT COUNT(*) FROM sit_ledger").fetchone()[0]
        self.assertEqual((100, 9), tuple(row))
        self.assertEqual(0, ledger_count)

    def test_daily_exposure_at_meeting_once(self):
        with closing(db.get_connection()) as conn:
            conn.execute("INSERT INTO daily_events(id,chat_id,date,time) VALUES (7,?,'2026-09-21','18:00')", (CHAT,))
            conn.executemany("INSERT INTO daily_participants(daily_id,user_id) VALUES (7,?)", [(1,), (2,)])
            conn.execute("UPDATE users SET cepen=5 WHERE user_id=1")
            conn.commit()
        with patch("cepen.random.random", return_value=0):
            self.assertEqual([], cepen.due_daily_exposures(datetime(2026, 9, 21, 17, 59)))
            found = cepen.due_daily_exposures(datetime(2026, 9, 21, 18, 0))
        self.assertEqual(1, len(found))
        self.assertIn("@second", found[0][1][0])
        self.assertEqual([], cepen.due_daily_exposures(datetime(2026, 9, 21, 18, 1)))

    def test_chat_switch_preserves_state_and_pauses_every_surface(self):
        with patch("cepen.random.random", return_value=0):
            cepen.attempt_primary(CHAT, 1, "coffee")
        self.assertEqual(1, settings.get_setting(CHAT, "enable_cepen"))
        settings.set_setting(CHAT, "enable_cepen", 0)
        self.assertEqual(0, settings.get_setting(CHAT, "enable_cepen"))
        self.assertEqual(5, cepen.length(CHAT, 1))
        self.assertEqual("Первый", db.get_user_display_name(1, CHAT))
        self.assertEqual("Цепень отключён в этом чате.", cepen.status_text(CHAT, 1))
        self.assertEqual("disabled", cepen.cure(CHAT, 1, price=50))
        with patch("cepen.random.random", return_value=0):
            self.assertIsNone(cepen.attempt_primary(CHAT, 2, "coffee"))
            self.assertIsNone(cepen.attempt_secondary(CHAT, 2, 1, "reply", 1))
            self.assertEqual([], cepen.attempt_event(CHAT, "daily", "off", [1, 2]))
        self.assertEqual({}, cepen.grow_all("2026-09-21"))
        labels = [button.text for row in settings.get_settings_keyboard(CHAT).inline_keyboard for button in row]
        self.assertIn("Включить цепня", labels)
        settings.set_setting(CHAT, "enable_cepen", 1)
        self.assertEqual("🪱 Первый", db.get_user_display_name(1, CHAT))
        with patch("cepen.random.random", return_value=0):
            self.assertIsNotNone(cepen.attempt_primary(CHAT, 2, "coffee"))
        self.assertIn(CHAT, cepen.grow_all("2026-09-21"))
        self.assertEqual(5.75, cepen.length(CHAT, 1))


if __name__ == "__main__":
    unittest.main()
