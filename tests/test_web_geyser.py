import sqlite3
import tempfile
import unittest
import gc
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import db
from web import server


class WebGeyserTests(unittest.TestCase):
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
            conn.execute(
                """
                INSERT INTO users (user_id, chat_id, name, sits, nick)
                VALUES (101, -500, 'Веб-игрок', 10, '@web_player')
                """
            )
            conn.commit()
        db.initialize_db()
        server._ensure_geyser_tables()

    def tearDown(self) -> None:
        db.DB_FILE = self.old_db_file
        gc.collect()
        self.temp_dir.cleanup()

    @patch("web.server.random.randint", return_value=500)
    def test_catch_returns_beneficiary_and_committed_reward(self, _randint) -> None:
        result = server._catch_geyser_for_today(user_id=101, chat_id=-500)

        self.assertEqual("Веб-игрок", result["beneficiary_name"])
        self.assertEqual(0.5, result["reward_sits"])
        self.assertEqual(10.5, result["balance"])
        self.assertEqual(1, result["caught_today"])

        with closing(db.get_connection()) as conn:
            row = conn.execute(
                "SELECT amount, action_code FROM sit_ledger ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(0.5, row["amount"])
        self.assertEqual("web_geyser_catch_reward", row["action_code"])


if __name__ == "__main__":
    unittest.main()
