import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import cepen_shop
import db
import settings


CHAT = -700


class CepenShopTests(unittest.TestCase):
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
                    PRIMARY KEY (user_id,chat_id)
                );
                CREATE TABLE settings (
                    chat_id INTEGER NOT NULL, name TEXT NOT NULL, value TEXT,
                    PRIMARY KEY (chat_id,name)
                );
            """)
            conn.execute(
                "INSERT INTO users(user_id,chat_id,name,nick,sits) VALUES (1,?,?,?,100)",
                (CHAT, "Первый", "@first"),
            )
            conn.execute(
                "INSERT INTO users(user_id,chat_id,name,nick,sits) VALUES (2,?,?,?,100)",
                (CHAT, "Второй", "@second"),
            )
            conn.commit()
        db.initialize_db()
        cepen_shop._PENDING.clear()

    def tearDown(self):
        cepen_shop._PENDING.clear()
        db.DB_FILE = self.old_db_file
        settings.DB_PATH = self.old_settings_db_file
        self.temp_dir.cleanup()

    def _callback(self, user_id=1):
        return SimpleNamespace(
            from_user=SimpleNamespace(id=user_id),
            message=SimpleNamespace(
                chat=SimpleNamespace(id=CHAT),
                edit_text=AsyncMock(),
            ),
            answer=AsyncMock(),
        )

    def _state(self, user_id=1):
        with closing(db.get_connection()) as conn:
            row = conn.execute(
                "SELECT cepen,sits FROM users WHERE chat_id=? AND user_id=?",
                (CHAT, user_id),
            ).fetchone()
            ledger = conn.execute("SELECT COUNT(*) FROM sit_ledger").fetchone()[0]
        return tuple(row), ledger

    def test_full_cure_requires_one_time_owner_confirmation(self):
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET cepen=100,cepen_name='Ого!' WHERE user_id=1")
            conn.commit()
        callback = self._callback()

        asyncio.run(cepen_shop.handle(callback, cepen_shop.FULL, confirmed=False))
        self.assertEqual(((100, 100), 0), self._state())
        edit = callback.message.edit_text.await_args
        self.assertIn("Подтвердить полное лечение", edit.args[0])
        data = edit.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        token = data.rsplit(":", 1)[-1]

        stranger = self._callback(user_id=2)
        asyncio.run(cepen_shop.handle(
            stranger, cepen_shop.FULL, confirmed=True, token=token
        ))
        self.assertEqual(((100, 100), 0), self._state())

        asyncio.run(cepen_shop.handle(
            callback, cepen_shop.FULL, confirmed=True, token=token
        ))
        self.assertEqual(((0, 50), 1), self._state())
        asyncio.run(cepen_shop.handle(
            callback, cepen_shop.FULL, confirmed=True, token=token
        ))
        self.assertEqual(((0, 50), 1), self._state())

    def test_partial_cure_preview_and_confirmation(self):
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET cepen=100,cepen_name='Ого!' WHERE user_id=1")
            conn.commit()
        callback = self._callback()

        asyncio.run(cepen_shop.handle(callback, cepen_shop.PARTIAL, confirmed=False))
        self.assertEqual(((100, 100), 0), self._state())
        edit = callback.message.edit_text.await_args
        self.assertIn("100 → 80 см", edit.args[0])
        token = edit.kwargs["reply_markup"].inline_keyboard[0][0].callback_data.rsplit(":", 1)[-1]

        asyncio.run(cepen_shop.handle(
            callback, cepen_shop.PARTIAL, confirmed=True, token=token
        ))
        self.assertEqual(((80, 90), 1), self._state())

    def test_expired_confirmation_does_nothing(self):
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET cepen=100 WHERE user_id=1")
            conn.commit()
        callback = self._callback()
        asyncio.run(cepen_shop.handle(callback, cepen_shop.FULL, confirmed=False))
        data = callback.message.edit_text.await_args.kwargs[
            "reply_markup"
        ].inline_keyboard[0][0].callback_data
        token = data.rsplit(":", 1)[-1]
        chat_id, user_id, kind, _ = cepen_shop._PENDING[token]
        cepen_shop._PENDING[token] = (chat_id, user_id, kind, 0)

        asyncio.run(cepen_shop.handle(
            callback, cepen_shop.FULL, confirmed=True, token=token
        ))
        self.assertEqual(((100, 100), 0), self._state())
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])


if __name__ == "__main__":
    unittest.main()
