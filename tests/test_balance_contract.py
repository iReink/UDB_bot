"""Transaction contract shared by Telegram and web balance operations."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import db


class BalanceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db, "DB_FILE", str(Path(self.temp_dir.name) / "stats.db"))
        self.db_patch.start()
        with closing(sqlite3.connect(db.DB_FILE)) as conn:
            conn.execute(
                """CREATE TABLE users (
                    user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                    name TEXT, sits REAL DEFAULT 0, punished INTEGER DEFAULT 0,
                    sex TEXT, nick TEXT, PRIMARY KEY (user_id, chat_id)
                )"""
            )
            conn.execute(
                "INSERT INTO users (user_id, chat_id, name, sits) VALUES (101, -500, 'Player', 10)"
            )
            conn.commit()
        db.initialize_db()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_caller_can_roll_back_balance_and_ledger_together(self) -> None:
        with closing(db.get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            db.apply_sit_change(
                conn, -500, 101, -2, action_code="test_purchase",
                action_ru="Тестовая покупка", require_sufficient=True,
            )
            conn.rollback()

        with closing(db.get_connection()) as conn:
            balance = conn.execute(
                "SELECT sits FROM users WHERE user_id=101 AND chat_id=-500"
            ).fetchone()[0]
            ledger_count = conn.execute("SELECT COUNT(*) FROM sit_ledger").fetchone()[0]
        self.assertEqual((balance, ledger_count), (10, 0))

    def test_failed_change_does_not_create_user_or_ledger_entry(self) -> None:
        with self.assertRaises(db.InsufficientSitsError):
            db.change_sits(
                -500, 202, -1, action_code="test_purchase",
                action_ru="Тестовая покупка", require_sufficient=True,
            )

        with closing(db.get_connection()) as conn:
            user_count = conn.execute(
                "SELECT COUNT(*) FROM users WHERE user_id=202 AND chat_id=-500"
            ).fetchone()[0]
            ledger_count = conn.execute("SELECT COUNT(*) FROM sit_ledger").fetchone()[0]
        self.assertEqual((user_count, ledger_count), (0, 0))

    def test_error_after_balance_update_rolls_back_transaction(self) -> None:
        with self.assertRaises(TypeError):
            db.change_sits(
                -500, 101, 2, action_code="test_income",
                action_ru="Тестовый доход", metadata={"invalid": object()},
            )

        with closing(db.get_connection()) as conn:
            balance = conn.execute(
                "SELECT sits FROM users WHERE user_id=101 AND chat_id=-500"
            ).fetchone()[0]
            ledger_count = conn.execute("SELECT COUNT(*) FROM sit_ledger").fetchone()[0]
            income_count = conn.execute("SELECT COUNT(*) FROM sit_stats").fetchone()[0]
        self.assertEqual((balance, ledger_count, income_count), (10, 0, 0))


if __name__ == "__main__":
    unittest.main()
