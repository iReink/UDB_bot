import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import db


CHAT = -700


class DickDuelTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_file = db.DB_FILE
        db.DB_FILE = str(Path(self.temp_dir.name) / "stats.db")
        with closing(sqlite3.connect(db.DB_FILE)) as conn:
            conn.execute(
                """
                CREATE TABLE dicks (
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    length INTEGER DEFAULT 0,
                    grow_date TEXT DEFAULT '',
                    buff TEXT DEFAULT '',
                    buff_exp TEXT DEFAULT '',
                    top1_entrance_date TEXT DEFAULT '',
                    PRIMARY KEY (user_id, chat_id)
                )
                """
            )
            conn.executemany(
                "INSERT INTO dicks(user_id,chat_id,length) VALUES (?,?,?)",
                [(1, CHAT, 121), (2, CHAT, 168)],
            )
            conn.commit()

        import dick

        self.dick = dick
        self.dick.ensure_dicks_table()

    def tearDown(self):
        db.DB_FILE = self.old_db_file
        self.temp_dir.cleanup()

    def test_duel_updates_both_lengths_atomically(self):
        result = self.dick.apply_duel_result(CHAT, 1, 2, 60)
        self.assertEqual((181, 108), result)
        with closing(db.get_connection()) as conn:
            rows = conn.execute(
                "SELECT user_id,length FROM dicks WHERE chat_id=? ORDER BY user_id",
                (CHAT,),
            ).fetchall()
        self.assertEqual([(1, 181), (2, 108)], [tuple(row) for row in rows])

    def test_duel_rejects_invalid_participants_or_bet(self):
        with self.assertRaises(ValueError):
            self.dick.apply_duel_result(CHAT, 1, 1, 60)
        with self.assertRaises(ValueError):
            self.dick.apply_duel_result(CHAT, 1, 2, 0)


if __name__ == "__main__":
    unittest.main()
