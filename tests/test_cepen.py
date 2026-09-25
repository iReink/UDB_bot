import asyncio
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from PIL import Image

import cepen
import cepen_avatar
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
            daily_messages_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cepen_daily_messages'"
            ).fetchone()
            scratches_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cepen_scratches'"
            ).fetchone()
            conn.execute("UPDATE users SET cepen=5, subscription_till='2999-01-01' WHERE user_id=1")
            conn.commit()
        self.assertIn("cepen", columns)
        self.assertIn("cepen_growth_date", columns)
        self.assertIn("cepen_name", columns)
        self.assertIn("cepen_profession", columns)
        self.assertIsNotNone(daily_messages_table)
        self.assertIsNotNone(scratches_table)
        self.assertEqual("👑 🐛 Первый", db.get_user_display_name(1, CHAT))

    def test_host_phrases_render_with_mention_and_signature(self):
        phrases = cepen.load_host_phrases()
        self.assertGreater(len(phrases), 0)
        self.assertTrue(all("{nickname}" in phrase for phrase in phrases))
        text = cepen.render_host_message(
            "{nickname}, внутри <уютнее>.", '<a href="tg://user?id=1">Первый</a>'
        )
        self.assertEqual(
            '<a href="tg://user?id=1">Первый</a>, внутри &lt;уютнее&gt;.\n– твой цепень ❤️',
            text,
        )
        named_text = cepen.render_host_message(
            "{nickname}, проверка.", "@first", "<Виталик>"
        )
        self.assertEqual(
            "@first, проверка.\n– твой цепень &lt;Виталик&gt; ❤️", named_text
        )

    def test_name_normalization_storage_and_full_cure_cleanup(self):
        for mark in cepen.CEPEN_NAME_DELETE_MARKS:
            self.assertEqual(("delete", None), cepen.normalize_name_input(f" {mark} "))
        self.assertEqual(("empty", None), cepen.normalize_name_input(" \n "))
        self.assertEqual(
            ("too_long", None),
            cepen.normalize_name_input("а" * (cepen.CEPEN_NAME_MAX_LENGTH + 1)),
        )
        self.assertEqual(("name", "Виталик Великий"), cepen.normalize_name_input(" Виталик   Великий "))

        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET cepen=5 WHERE user_id=1")
            conn.commit()
        self.assertEqual("named", cepen.set_name(CHAT, 1, "Виталик"))
        self.assertEqual("Виталик", cepen.name(CHAT, 1))
        self.assertIn("🐛 Цепень Виталик: 5 см", cepen.status_text(CHAT, 1))
        self.assertEqual("cured", cepen.cure(CHAT, 1))
        self.assertIsNone(cepen.name(CHAT, 1))

    def test_name_fsm_sets_and_deletes_name(self):
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET cepen=5 WHERE user_id=1")
            conn.commit()

        async def scenario():
            dp = Dispatcher()
            cepen.register_handlers(dp)
            bot = Bot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
            bot.session = AsyncMock(return_value=True)

            async def click_name():
                update = Update.model_validate({
                    "update_id": 1,
                    "callback_query": {
                        "id": "callback",
                        "chat_instance": "chat",
                        "from": {"id": 1, "is_bot": False, "first_name": "Первый"},
                        "data": "cepen:name:1",
                        "message": {
                            "message_id": 10,
                            "date": 1700000000,
                            "chat": {"id": CHAT, "type": "supergroup"},
                        },
                    },
                })
                await dp.feed_update(bot, update)

            async def send_text(text):
                update = Update.model_validate({
                    "update_id": 2,
                    "message": {
                        "message_id": 11,
                        "date": 1700000001,
                        "chat": {"id": CHAT, "type": "supergroup"},
                        "from": {"id": 1, "is_bot": False, "first_name": "Первый"},
                        "text": text,
                    },
                })
                await dp.feed_update(bot, update)

            state = dp.fsm.get_context(bot=bot, chat_id=CHAT, user_id=1)
            await click_name()
            self.assertEqual(await state.get_state(), cepen.CepenNameStates.waiting_for_name.state)
            await send_text("  Виталик   Великий  ")
            self.assertIsNone(await state.get_state())
            self.assertEqual("Виталик Великий", cepen.name(CHAT, 1))

            await click_name()
            await send_text(" — ")
            self.assertIsNone(await state.get_state())
            self.assertIsNone(cepen.name(CHAT, 1))

        asyncio.run(scenario())

    def test_host_message_schedule_is_daily_durable_and_respects_state(self):
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET cepen=5 WHERE user_id=1")
            conn.commit()
        morning = datetime(2026, 9, 22, 9, 0)
        with patch("cepen.random.randint", return_value=0), patch(
            "cepen.random.choice", return_value="{nickname}, проверка связи."
        ):
            self.assertEqual(1, cepen.schedule_host_messages(morning))
            self.assertEqual(0, cepen.schedule_host_messages(morning))
        with closing(db.get_connection()) as conn:
            row = conn.execute(
                "SELECT scheduled_at,phrase,sent_at FROM cepen_daily_messages"
            ).fetchone()
        self.assertEqual("2026-09-22 10:00:00", row["scheduled_at"])
        self.assertEqual("{nickname}, проверка связи.", row["phrase"])
        self.assertIsNone(row["sent_at"])
        self.assertEqual([], cepen.due_host_messages(datetime(2026, 9, 22, 9, 59, 59)))
        due = cepen.due_host_messages(datetime(2026, 9, 22, 10, 0))
        self.assertEqual([(CHAT, 1)], [(item.chat_id, item.user_id) for item in due])

        settings.set_setting(CHAT, "enable_cepen", 0)
        self.assertEqual([], cepen.due_host_messages(datetime(2026, 9, 22, 10, 1)))
        settings.set_setting(CHAT, "enable_cepen", 1)
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET cepen=0 WHERE user_id=1")
            conn.commit()
        self.assertEqual([], cepen.due_host_messages(datetime(2026, 9, 22, 10, 1)))
        self.assertEqual(0, cepen.schedule_host_messages(datetime(2026, 9, 22, 23, 0)))

    def test_host_message_dispatch_marks_success_and_does_not_repeat(self):
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET cepen=5,cepen_name='Виталик' WHERE user_id=1")
            conn.commit()
        now = datetime(2026, 9, 22, 12, 0)
        with patch("cepen.random.randint", return_value=0), patch(
            "cepen.random.choice", return_value="{nickname}, проверка связи."
        ):
            cepen.schedule_host_messages(now)
        bot = AsyncMock()
        self.assertEqual(1, asyncio.run(cepen.dispatch_host_messages(bot, now)))
        bot.send_message.assert_awaited_once()
        args, kwargs = bot.send_message.await_args
        self.assertEqual(CHAT, args[0])
        self.assertIn("@first, проверка связи.\n– твой цепень Виталик ❤️", args[1])
        self.assertEqual("HTML", kwargs["parse_mode"])
        self.assertEqual(0, asyncio.run(cepen.dispatch_host_messages(bot, now)))
        bot.send_message.assert_awaited_once()

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

    def test_reply_exposure_ignores_commands_bots_and_self_replies(self):
        user = SimpleNamespace(id=2, is_bot=False)
        source = SimpleNamespace(id=1, is_bot=False)
        bot_source = SimpleNamespace(id=99, is_bot=True)
        command_entity = SimpleNamespace(type="bot_command", offset=0)

        plain_reply = SimpleNamespace(
            from_user=user,
            reply_to_message=SimpleNamespace(from_user=source),
            text="обычный ответ",
            caption=None,
            entities=[],
            caption_entities=[],
        )
        self.assertEqual(1, cepen.reply_exposure_source_id(plain_reply))

        command_reply = SimpleNamespace(
            **{**plain_reply.__dict__, "text": "/shop@udb_flood_bot", "entities": [command_entity]}
        )
        self.assertIsNone(cepen.reply_exposure_source_id(command_reply))

        command_without_entities = SimpleNamespace(
            **{**plain_reply.__dict__, "text": "/shop"}
        )
        self.assertIsNone(cepen.reply_exposure_source_id(command_without_entities))

        self_reply = SimpleNamespace(
            **{**plain_reply.__dict__, "reply_to_message": SimpleNamespace(from_user=user)}
        )
        self.assertIsNone(cepen.reply_exposure_source_id(self_reply))

        bot_reply = SimpleNamespace(
            **{**plain_reply.__dict__, "reply_to_message": SimpleNamespace(from_user=bot_source)}
        )
        self.assertIsNone(cepen.reply_exposure_source_id(bot_reply))

    def test_infection_instruction_does_not_tag_doctor(self):
        self.assertNotIn("@jprgprh", cepen.INSTRUCTION)
        self.assertIn("официальный дядя доктор", cepen.INSTRUCTION)

    def test_cepen_menu_and_rankings(self):
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET cepen=30 WHERE user_id=1")
            conn.execute("UPDATE users SET cepen=20 WHERE user_id=2")
            conn.execute("UPDATE users SET cepen_name='Виталик' WHERE user_id=1")
            conn.execute("UPDATE users SET cepen=0 WHERE user_id=3")
            conn.executemany(
                "INSERT INTO users(user_id,chat_id,name,nick,cepen) VALUES (?,?,?,?,?)",
                [
                    (user_id, CHAT, f"Игрок {user_id}", f"user{user_id}", float(user_id))
                    for user_id in range(4, 14)
                ],
            )
            conn.commit()

        infected_keyboard = cepen.menu_keyboard(
            1, has_cepen=True, cepen_name="Виталик", scratch_count=15
        )
        infected_buttons = [
            button for row in infected_keyboard.inline_keyboard for button in row
        ]
        self.assertEqual(
            [
                "Почесать Виталик [15/50]",
                "Изменить имя цепня",
                "Выбрать профессию",
                "Рейтинг цепней",
            ],
            [button.text for button in infected_buttons],
        )
        self.assertEqual(
            [
                "cepen:scratch:1",
                "cepen:name:1",
                "cepen:profession:1",
                "cepen:rating:1",
            ],
            [button.callback_data for button in infected_buttons],
        )
        healthy_keyboard = cepen.menu_keyboard(3, has_cepen=False)
        self.assertEqual(
            ["Рейтинг цепней"],
            [button.text for row in healthy_keyboard.inline_keyboard for button in row],
        )

        short_text, total = cepen.ranking_text(CHAT)
        self.assertEqual(12, total)
        self.assertEqual(10, len(short_text.splitlines()) - 1)
        self.assertIn("1. 🐛 Первый — Виталик — 30 см", short_text)
        self.assertNotIn("@user4", short_text)
        full_button = cepen.rating_keyboard(1, total)
        self.assertEqual(
            "cepen:rating_full:1", full_button.inline_keyboard[0][0].callback_data
        )

        full_text, full_total = cepen.ranking_text(CHAT, full=True)
        self.assertEqual(12, full_total)
        self.assertEqual(12, len(full_text.splitlines()) - 1)
        self.assertIn("12. 🐛 Игрок 4 — 4 см", full_text)
        self.assertIsNone(cepen.rating_keyboard(1, full_total, full=True))

    def test_avatar_levels_render_base_and_profession_layers(self):
        values = [0, 15, 15.001, 30, 31, 60, 61, 120, 121, 200,
                  201, 300, 301, 400, 401, 500, 501, 700, 701]
        self.assertEqual(
            [1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10],
            [cepen_avatar.level_for_length(value) for value in values],
        )
        with tempfile.TemporaryDirectory() as cache:
            base = cepen_avatar.render_avatar(5, cache_dir=cache)
            gamer = cepen_avatar.render_avatar(701, "gamer", cache_dir=cache)
            self.assertNotEqual(base.read_bytes(), gamer.read_bytes())
            with Image.open(base) as image:
                self.assertEqual((1024, 1024), image.size)
                self.assertEqual("RGB", image.mode)
            with Image.open(gamer) as image:
                self.assertEqual((1024, 1024), image.size)

    def test_profession_menu_and_atomic_paid_changes(self):
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET cepen=25,sits=10 WHERE user_id=1")
            conn.commit()

        keyboard = cepen.profession_keyboard(1, None)
        buttons = [button for row in keyboard.inline_keyboard for button in row]
        self.assertEqual(11, len(buttons))
        self.assertEqual("cepen:profession_set:1:designer", buttons[0].callback_data)
        self.assertEqual("cepen:back:1", buttons[-1].callback_data)
        self.assertIn("Первый выбор бесплатный", cepen.profession_menu_text(None, None))

        first = cepen.set_profession(CHAT, 1, "designer")
        self.assertEqual(cepen.ProfessionResult("changed", "designer", 0), first)
        self.assertEqual("same", cepen.set_profession(CHAT, 1, "designer").status)
        second = cepen.set_profession(CHAT, 1, "programmer")
        self.assertEqual(cepen.ProfessionResult("changed", "programmer", 5), second)
        third = cepen.set_profession(CHAT, 1, "doctor")
        self.assertEqual(cepen.ProfessionResult("changed", "doctor", 5), third)
        self.assertEqual("insufficient", cepen.set_profession(CHAT, 1, "teacher").status)
        self.assertEqual("doctor", cepen.profession(CHAT, 1))

        with closing(db.get_connection()) as conn:
            user = conn.execute(
                "SELECT sits,cepen_profession FROM users WHERE chat_id=? AND user_id=1",
                (CHAT,),
            ).fetchone()
            ledger = conn.execute(
                "SELECT amount,action_code FROM sit_ledger "
                "WHERE action_code='cepen_profession_change' ORDER BY id"
            ).fetchall()
        self.assertEqual((0, "doctor"), tuple(user))
        self.assertEqual(
            [(-5, "cepen_profession_change"), (-5, "cepen_profession_change")],
            [tuple(row) for row in ledger],
        )
        self.assertEqual("cured", cepen.cure(CHAT, 1))
        self.assertIsNone(cepen.profession(CHAT, 1))

    def test_cepen_command_sends_avatar_as_photo(self):
        with closing(db.get_connection()) as conn:
            conn.execute(
                "UPDATE users SET cepen=31,cepen_profession='artist' WHERE user_id=1"
            )
            conn.commit()

        async def scenario():
            dp = Dispatcher()
            cepen.register_handlers(dp)
            bot = Bot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
            bot.session = AsyncMock(return_value=True)
            update = Update.model_validate({
                "update_id": 41,
                "message": {
                    "message_id": 20,
                    "date": 1700000000,
                    "chat": {"id": CHAT, "type": "supergroup"},
                    "from": {"id": 1, "is_bot": False, "first_name": "Первый"},
                    "text": "/cepen",
                    "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
                },
            })
            await dp.feed_update(bot, update)
            method = bot.session.await_args.args[1]
            self.assertEqual("SendPhoto", type(method).__name__)
            self.assertIn("Длина цепня: 31 см", method.caption)
            self.assertEqual("Сменить профессию", method.reply_markup.inline_keyboard[2][0].text)

        asyncio.run(scenario())

    def test_profession_callback_replaces_photo_and_keeps_submenu(self):
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET cepen=31,sits=10 WHERE user_id=1")
            conn.commit()

        async def scenario():
            dp = Dispatcher()
            cepen.register_handlers(dp)
            bot = Bot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
            bot.session = AsyncMock(return_value=True)
            update = Update.model_validate({
                "update_id": 42,
                "callback_query": {
                    "id": "profession-callback",
                    "chat_instance": "chat",
                    "from": {"id": 1, "is_bot": False, "first_name": "Первый"},
                    "data": "cepen:profession_set:1:scientist",
                    "message": {
                        "message_id": 21,
                        "date": 1700000000,
                        "chat": {"id": CHAT, "type": "supergroup"},
                        "photo": [{
                            "file_id": "AgACAgIAAxkBAAIB",
                            "file_unique_id": "AQAD-avatar",
                            "width": 1024,
                            "height": 1024,
                            "file_size": 1000,
                        }],
                        "caption": "old",
                    },
                },
            })
            await dp.feed_update(bot, update)
            methods = [type(call.args[1]).__name__ for call in bot.session.await_args_list]
            self.assertIn("AnswerCallbackQuery", methods)
            self.assertIn("EditMessageMedia", methods)
            edit_call = next(
                call for call in bot.session.await_args_list
                if type(call.args[1]).__name__ == "EditMessageMedia"
            )
            edit_method = edit_call.args[1]
            self.assertIn("Цепень-учёный", edit_method.media.caption)
            self.assertEqual("✓ 🧪 Учёный", edit_method.reply_markup.inline_keyboard[2][1].text)

        asyncio.run(scenario())
        self.assertEqual("scientist", cepen.profession(CHAT, 1))
        with closing(db.get_connection()) as conn:
            balance = conn.execute(
                "SELECT sits FROM users WHERE chat_id=? AND user_id=1", (CHAT,)
            ).fetchone()[0]
        self.assertEqual(10, balance)

    def test_scratching_rewards_owner_and_enforces_daily_limits(self):
        today = datetime.now().date().isoformat()
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET cepen=25,cepen_name='Виталик' WHERE user_id=1")
            conn.executemany(
                "INSERT INTO users(user_id,chat_id,name,nick,sits) VALUES (?,?,?,?,0)",
                [
                    (user_id, CHAT, f"Игрок {user_id}", f"user{user_id}")
                    for user_id in range(4, 13)
                ],
            )
            conn.commit()

        self.assertEqual(
            "self",
            cepen.scratch(CHAT, 1, 1, "self", date_key=today).status,
        )
        first = cepen.scratch(CHAT, 1, 2, "click-1", date_key=today)
        self.assertEqual(("scratched", 1, 1), tuple(first.__dict__.values()))
        duplicate = cepen.scratch(CHAT, 1, 2, "click-1", date_key=today)
        self.assertEqual(("duplicate", 1, 1), tuple(duplicate.__dict__.values()))
        for number in range(2, 6):
            self.assertEqual(
                "scratched",
                cepen.scratch(
                    CHAT, 1, 2, f"click-{number}", date_key=today
                ).status,
            )
        self.assertEqual(
            "user_limit",
            cepen.scratch(CHAT, 1, 2, "click-6", date_key=today).status,
        )

        callback_number = 10
        for scratcher_id in range(3, 12):
            for _ in range(5):
                callback_number += 1
                self.assertEqual(
                    "scratched",
                    cepen.scratch(
                        CHAT,
                        1,
                        scratcher_id,
                        f"click-{callback_number}",
                        date_key=today,
                    ).status,
                )
        self.assertEqual(
            "worm_limit",
            cepen.scratch(CHAT, 1, 12, "over-total", date_key=today).status,
        )

        rows, total = cepen.scratch_summary(CHAT, 1, date_key=today)
        self.assertEqual(50, total)
        self.assertEqual(("Второй", 5), rows[0])
        text = cepen.status_text(CHAT, 1)
        self.assertIn("Сегодня чесали: Второй (5)", text)
        self.assertIn("Получено 5 сит.", text)
        self.assertIn("Друзья могут чесать твоего цепня и ты получишь сит.", text)
        self.assertNotIn("Виталик", cepen._manual_text("Виталик"))
        with closing(db.get_connection()) as conn:
            balance = conn.execute(
                "SELECT sits FROM users WHERE chat_id=? AND user_id=1", (CHAT,)
            ).fetchone()[0]
            ledger = conn.execute(
                "SELECT COUNT(*),SUM(amount) FROM sit_ledger "
                "WHERE action_code='cepen_scratch_reward'"
            ).fetchone()
        self.assertEqual(15, balance)
        self.assertEqual((50, 5), tuple(ledger))

    def test_concurrent_scratches_do_not_exceed_personal_limit(self):
        today = datetime.now().date().isoformat()
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET cepen=5 WHERE user_id=1")
            conn.commit()

        def click(number):
            return cepen.scratch(
                CHAT, 1, 2, f"parallel-{number}", date_key=today
            ).status

        with ThreadPoolExecutor(max_workers=10) as pool:
            statuses = list(pool.map(click, range(20)))
        self.assertEqual(5, statuses.count("scratched"))
        self.assertEqual(15, statuses.count("user_limit"))
        with closing(db.get_connection()) as conn:
            balance = conn.execute(
                "SELECT sits FROM users WHERE chat_id=? AND user_id=1", (CHAT,)
            ).fetchone()[0]
            events = conn.execute("SELECT COUNT(*) FROM cepen_scratches").fetchone()[0]
        self.assertEqual(10.5, balance)
        self.assertEqual(5, events)

    def test_directional_pair_probabilities(self):
        self.assertEqual(
            {
                "sos": (.45, .05),
                "shpeh": (.49, .06),
                "bite": (.475, .015),
                "duel": (.10, .10),
            },
            cepen.PAIR_CHANCES,
        )
        self.assertEqual(
            {"group_participant": .075, "group_spectator": .025, "daily": .075},
            cepen.GROUP_CHANCES,
        )
        self.assertEqual(.0025, cepen.REPLY_CHANCE)
        self.assertEqual(
            {"geyser": .025, "coffee": .004, "round": .003, "sticker": .0006},
            cepen.PRIMARY_CHANCES,
        )
        with patch("cepen.random.random", return_value=0):
            cepen.attempt_primary(CHAT, 2, "coffee")
        self.assertEqual("named", cepen.set_name(CHAT, 2, "Виталик"))
        with patch("cepen.random.random", return_value=.4):
            text = cepen.attempt_pair(CHAT, 1, 2, "sos")
        self.assertIn("@first засосал @second", text)
        self.assertIn("цепня по имени Виталик", text)
        with patch("cepen.random.random", return_value=.5):
            self.assertIsNone(cepen.attempt_pair(CHAT, 2, 3, "sos"))
        with patch("cepen.random.random", return_value=.04):
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
            conn.execute("UPDATE users SET cepen=100,sits=10,cepen_name='Виталик' WHERE user_id=1")
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
        self.assertIn("цепень Виталик", report)
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
        self.assertNotIn("🍆", preview)
        self.assertNotIn("Баланс:", preview)
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
        self.assertNotIn("🍆", text)

        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET sits=10 WHERE user_id=1")
            conn.commit()
        text = cepen.status_text(CHAT, 1)
        self.assertIn("полный рост до 115 см", text)
        self.assertIn("Член вырастет на 10 см", text)

    def test_partial_cure_is_atomic_repeatable_and_has_floor(self):
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE users SET cepen=100,sits=25,cepen_name='Виталик' WHERE user_id=2")
            conn.commit()
        self.assertEqual(("reduced", 100, 80), cepen.partial_cure(CHAT, 2))
        self.assertEqual(("reduced", 80, 64), cepen.partial_cure(CHAT, 2))
        with closing(db.get_connection()) as conn:
            row = conn.execute("SELECT cepen,sits,cepen_name FROM users WHERE user_id=2").fetchone()
            ledger = conn.execute(
                "SELECT amount,action_code FROM sit_ledger ORDER BY id"
            ).fetchall()
            conn.execute("UPDATE users SET cepen=5 WHERE user_id=2")
            conn.commit()
        self.assertEqual((64, 5, "Виталик"), tuple(row))
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
        self.assertEqual("🐛 Первый", db.get_user_display_name(1, CHAT))
        with patch("cepen.random.random", return_value=0):
            self.assertIsNotNone(cepen.attempt_primary(CHAT, 2, "coffee"))
        self.assertIn(CHAT, cepen.grow_all("2026-09-21"))
        self.assertEqual(5.75, cepen.length(CHAT, 1))


if __name__ == "__main__":
    unittest.main()
