import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path

import db


class SitLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_file = db.DB_FILE
        db.DB_FILE = str(Path(self.temp_dir.name) / "stats.db")
        with closing(sqlite3.connect(db.DB_FILE)) as conn:
            conn.execute(
                """
                CREATE TABLE users (
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    name TEXT,
                    sits REAL DEFAULT 0,
                    punished INTEGER DEFAULT 0,
                    sex TEXT,
                    nick TEXT,
                    PRIMARY KEY (user_id, chat_id)
                )
                """
            )
            conn.commit()
            conn.execute(
                """
                INSERT INTO users (user_id, chat_id, name, sits, nick)
                VALUES (101, -500, 'Тестовый игрок', 10, 'test_player')
                """
            )
            conn.commit()
        db.initialize_db()

    def tearDown(self) -> None:
        db.DB_FILE = self.old_db_file
        self.temp_dir.cleanup()

    def test_income_and_expense_are_audited_with_balance_snapshots(self) -> None:
        db.change_sits(
            -500,
            101,
            2.5,
            action_code="test_income",
            action_ru="Тестовый доход",
        )
        db.change_sits(
            -500,
            101,
            -1.25,
            action_code="test_expense",
            action_ru="Тестовый расход",
            require_sufficient=True,
        )

        with closing(db.get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT user_id, chat_id, nick, amount, balance_before,
                       balance_after, action_code, action_ru
                FROM sit_ledger
                ORDER BY id
                """
            ).fetchall()
            balance = conn.execute(
                "SELECT sits FROM users WHERE user_id = 101 AND chat_id = -500"
            ).fetchone()["sits"]

        self.assertEqual(12.5, rows[0]["balance_after"])
        self.assertEqual(-1.25, rows[1]["amount"])
        self.assertEqual(12.5, rows[1]["balance_before"])
        self.assertEqual(11.25, rows[1]["balance_after"])
        self.assertEqual("@test_player", rows[1]["nick"])
        self.assertEqual("test_expense", rows[1]["action_code"])
        self.assertEqual("Тестовый расход", rows[1]["action_ru"])
        self.assertEqual(11.25, balance)

    def test_insufficient_expense_changes_neither_balance_nor_ledger(self) -> None:
        with self.assertRaises(db.InsufficientSitsError):
            db.change_sits(
                -500,
                101,
                -11,
                action_code="too_large",
                action_ru="Слишком большой расход",
                require_sufficient=True,
            )

        with closing(db.get_connection()) as conn:
            balance = conn.execute(
                "SELECT sits FROM users WHERE user_id = 101 AND chat_id = -500"
            ).fetchone()["sits"]
            ledger_count = conn.execute("SELECT COUNT(*) FROM sit_ledger").fetchone()[0]

        self.assertEqual(10, balance)
        self.assertEqual(0, ledger_count)

    def test_geyser_claim_is_atomic_idempotent_and_accepts_zero_reward(self) -> None:
        today = datetime.now().date().isoformat()
        current_time = datetime.now().strftime("%H:%M")
        with closing(db.get_connection()) as conn:
            conn.execute(
                """
                INSERT INTO geyser_events (
                    chat_id, date, scheduled_time, status, message_id
                )
                VALUES (-500, ?, ?, 'sent', 9001)
                """,
                (today, "00:01"),
            )
            first_event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                """
                INSERT INTO geyser_events (
                    chat_id, date, scheduled_time, status, message_id
                )
                VALUES (-500, ?, ?, 'sent', 9002)
                """,
                (today, current_time),
            )
            zero_event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()

        self.assertTrue(db.claim_geyser_event_with_reward(first_event_id, -500, 9001, 101, 2))
        self.assertFalse(db.claim_geyser_event_with_reward(first_event_id, -500, 9001, 101, 2))
        self.assertTrue(db.claim_geyser_event_with_reward(zero_event_id, -500, 9002, 101, 0))

        with closing(db.get_connection()) as conn:
            balance = conn.execute(
                "SELECT sits FROM users WHERE user_id = 101 AND chat_id = -500"
            ).fetchone()["sits"]
            ledger_rows = conn.execute(
                "SELECT amount, action_code FROM sit_ledger ORDER BY id"
            ).fetchall()
            statuses = conn.execute(
                "SELECT status, caught_by FROM geyser_events ORDER BY id"
            ).fetchall()

        self.assertEqual(12, balance)
        self.assertEqual([(2, "geyser_catch_reward")], [tuple(row) for row in ledger_rows])
        self.assertEqual([("caught", 101), ("caught", 101)], [tuple(row) for row in statuses])


if __name__ == "__main__":
    unittest.main()
