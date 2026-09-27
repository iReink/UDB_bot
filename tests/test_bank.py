import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bank_core
import bank_bot
import db


CHAT = -500
USER = 101


class BankMenuOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_foreign_callback_is_rejected(self) -> None:
        query = SimpleNamespace(
            data=bank_bot._callback(USER, "deposit"),
            from_user=SimpleNamespace(id=202),
            answer=AsyncMock(),
        )
        self.assertIsNone(await bank_bot._require_menu_owner(query))
        query.answer.assert_awaited_once_with(
            "Это меню другого пользователя. Вызовите /bank, чтобы открыть своё.",
            show_alert=True,
        )


class BankTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(
            db, "DB_FILE", str(Path(self.temp_dir.name) / "stats.db")
        )
        self.db_patch.start()
        with closing(sqlite3.connect(db.DB_FILE)) as conn:
            conn.executescript(
                """
                CREATE TABLE users (
                    user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                    name TEXT, sits REAL DEFAULT 0, punished INTEGER DEFAULT 0,
                    sex TEXT, nick TEXT, PRIMARY KEY (user_id, chat_id)
                );
                CREATE TABLE daily_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                    date TEXT NOT NULL, messages INTEGER DEFAULT 0,
                    words INTEGER DEFAULT 0, chars INTEGER DEFAULT 0,
                    stickers INTEGER DEFAULT 0, coffee INTEGER DEFAULT 0,
                    react_given INTEGER DEFAULT 0, react_taken INTEGER DEFAULT 0,
                    rounds INTEGER DEFAULT 0, bites_given INTEGER DEFAULT 0,
                    bites_received INTEGER DEFAULT 0, profanity_count INTEGER DEFAULT 0,
                    UNIQUE(user_id, chat_id, date)
                );
                INSERT INTO users (user_id, chat_id, name, sits)
                VALUES (101, -500, 'Player', 200), (202, -500, 'Receiver', 0);
                """
            )
            conn.commit()
        db.initialize_db()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _add_message_history(self, first: date, days: int = 10) -> None:
        with closing(db.get_connection()) as conn:
            for offset in range(days):
                conn.execute(
                    """
                    INSERT INTO daily_stats (user_id, chat_id, date, messages)
                    VALUES (?, ?, ?, 1)
                    """,
                    (USER, CHAT, (first + timedelta(days=offset)).isoformat()),
                )
            conn.commit()

    def test_system_income_is_split_and_audited(self) -> None:
        db.change_sits(
            CHAT, USER, 10,
            action_code="quest_reward", action_ru="Квест",
        )
        with closing(db.get_connection()) as conn:
            user = conn.execute(
                "SELECT sits FROM users WHERE chat_id=? AND user_id=?", (CHAT, USER)
            ).fetchone()
            account = conn.execute(
                "SELECT liquidity_milli,capital_milli FROM bank_accounts WHERE chat_id=?",
                (CHAT,),
            ).fetchone()
            ledger = conn.execute(
                "SELECT amount,metadata_json FROM sit_ledger ORDER BY id DESC LIMIT 1"
            ).fetchone()
            income = conn.execute(
                "SELECT amount_milli FROM bank_daily_income WHERE chat_id=? AND user_id=?",
                (CHAT, USER),
            ).fetchone()
        metadata = json.loads(ledger["metadata_json"])
        self.assertEqual(209.5, user["sits"])
        self.assertEqual((50_500, 50_500), tuple(account))
        self.assertEqual(9.5, ledger["amount"])
        self.assertEqual(500, metadata["bank_tax_milli"])
        self.assertEqual(9_500, income["amount_milli"])

    def test_personal_bank_menu_hides_global_and_empty_contract_details(self) -> None:
        with closing(db.get_connection()) as conn:
            bank_core.ensure_account(conn, CHAT, datetime(2026, 9, 1, 10, 0))
            state = bank_bot._user_bank_state(conn, CHAT, USER)
            text = bank_bot._personal_menu_text(conn, CHAT, USER, state)
        self.assertIn("Выберите действие", text)
        self.assertNotIn("Ликвидность", text)
        self.assertNotIn("Средний доход", text)
        self.assertNotIn("Исходный лимит", text)
        self.assertNotIn("активного кредита нет", text)
        self.assertNotIn("активного вклада нет", text)

        keyboard = bank_bot._menu_keyboard(USER, managed=False, has_contracts=False)
        labels = [button.text for row in keyboard.inline_keyboard for button in row]
        callbacks = [
            button.callback_data for row in keyboard.inline_keyboard for button in row
        ]
        self.assertNotIn("👤 Мои договоры", labels)
        self.assertNotIn("🛠 Управление", labels)
        self.assertTrue(all(value.startswith(f"bank:{USER}:") for value in callbacks))

    def test_personal_bank_menu_shows_only_material_user_state(self) -> None:
        opened = datetime(2026, 9, 1, 10, 0)
        with closing(db.get_connection()) as conn:
            bank_core.ensure_account(conn, CHAT, opened)
            bank_core.credit_profile(conn, CHAT, USER)
            conn.execute(
                """
                UPDATE bank_credit_profiles SET default_debt_milli=12345
                WHERE chat_id=? AND user_id=?
                """,
                (CHAT, USER),
            )
            bank_core.open_deposit(
                conn, CHAT, USER, 100_000, 1, False, db.apply_sit_change, now=opened
            )
            state = bank_bot._user_bank_state(conn, CHAT, USER)
            text = bank_bot._personal_menu_text(conn, CHAT, USER, state)
            contracts = bank_bot._mine_text(conn, CHAT, USER, state)
            conn.commit()
        self.assertIn("Остаток долга после дефолта", text)
        self.assertIn("Выплата по вкладу", text)
        self.assertNotIn("Средний доход", contracts)
        self.assertNotIn("Исходный лимит", contracts)
        self.assertNotIn("активного кредита нет", contracts)

        keyboard = bank_bot._menu_keyboard(USER, managed=True, has_contracts=True)
        labels = [button.text for row in keyboard.inline_keyboard for button in row]
        self.assertIn("👤 Мои договоры", labels)
        self.assertIn("🛠 Управление", labels)

    def test_contract_actions_are_conditional_and_owner_bound(self) -> None:
        state = {
            "deposit": {"id": 1},
            "loan": None,
            "overdue_count": 0,
            "overdue_milli": 0,
            "rating": 0,
            "default_debt_milli": 0,
            "claim_milli": 0,
        }
        keyboard = bank_bot._mine_keyboard(USER, state)
        buttons = [button for row in keyboard.inline_keyboard for button in row]
        labels = [button.text for button in buttons]
        self.assertIn("Автопродление вкл/выкл", labels)
        self.assertIn("Закрыть вклад досрочно", labels)
        self.assertNotIn("Погасить просрочки", labels)
        self.assertNotIn("Погасить кредит досрочно", labels)
        self.assertTrue(
            all(button.callback_data.startswith(f"bank:{USER}:") for button in buttons)
        )

    def test_p2p_is_taxed_but_excluded_from_credit_income(self) -> None:
        db.change_sits(
            CHAT, 202, 10,
            action_code="transfer_received", action_ru="Перевод",
        )
        with closing(db.get_connection()) as conn:
            balance = conn.execute(
                "SELECT sits FROM users WHERE chat_id=? AND user_id=202", (CHAT,)
            ).fetchone()[0]
            income_count = conn.execute(
                "SELECT COUNT(*) FROM bank_daily_income WHERE chat_id=? AND user_id=202",
                (CHAT,),
            ).fetchone()[0]
        self.assertEqual(9.5, balance)
        self.assertEqual(0, income_count)

    def test_charity_is_fully_exempt(self) -> None:
        db.change_sits(
            CHAT, USER, 10,
            action_code="admin_charity_grant", action_ru="Благотворительность",
        )
        with closing(db.get_connection()) as conn:
            balance = conn.execute(
                "SELECT sits FROM users WHERE chat_id=? AND user_id=?", (CHAT, USER)
            ).fetchone()[0]
            account_count = conn.execute(
                "SELECT COUNT(*) FROM bank_accounts WHERE chat_id=?", (CHAT,)
            ).fetchone()[0]
        self.assertEqual(210, balance)
        self.assertEqual(0, account_count)

    def test_default_garnishment_uses_gross_income(self) -> None:
        with closing(db.get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            bank_core.credit_profile(conn, CHAT, USER)
            conn.execute(
                """
                UPDATE bank_credit_profiles SET default_debt_milli=100000
                WHERE chat_id=? AND user_id=?
                """,
                (CHAT, USER),
            )
            conn.commit()
        db.change_sits(
            CHAT, USER, 100,
            action_code="quest_reward", action_ru="Квест",
        )
        with closing(db.get_connection()) as conn:
            balance = conn.execute(
                "SELECT sits FROM users WHERE chat_id=? AND user_id=?", (CHAT, USER)
            ).fetchone()[0]
            profile = conn.execute(
                "SELECT default_debt_milli FROM bank_credit_profiles WHERE chat_id=? AND user_id=?",
                (CHAT, USER),
            ).fetchone()
            account = conn.execute(
                "SELECT liquidity_milli,capital_milli FROM bank_accounts WHERE chat_id=?",
                (CHAT,),
            ).fetchone()
        self.assertEqual(215, balance)
        self.assertEqual(20_000, profile["default_debt_milli"])
        self.assertEqual((135_000, 55_000), tuple(account))

    def test_deposit_reserves_capital_and_moves_real_liquidity(self) -> None:
        opened = datetime(2026, 9, 1, 10, 0)
        with closing(db.get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            deposit_id = bank_core.open_deposit(
                conn, CHAT, USER, 100_000, 1, True,
                db.apply_sit_change, now=opened,
            )
            metrics = bank_core.bank_metrics(conn, CHAT, opened.date())
            deposit = conn.execute(
                "SELECT * FROM bank_deposits WHERE id=?", (deposit_id,)
            ).fetchone()
            conn.commit()
        self.assertEqual("2026-09-08", deposit["maturity_date"])
        self.assertEqual(103_000, deposit["maturity_milli"])
        self.assertGreater(metrics["reserved_capital_milli"], 3_000)
        self.assertEqual(150_000, metrics["liquidity_milli"])
        with closing(db.get_connection()) as conn:
            balance = conn.execute(
                "SELECT sits FROM users WHERE chat_id=? AND user_id=?", (CHAT, USER)
            ).fetchone()[0]
        self.assertEqual(100, balance)

    def test_credit_schedule_uses_real_liquidity_and_exact_total(self) -> None:
        issue_day = date(2026, 9, 10)
        self._add_message_history(date(2026, 8, 29), 20)
        with closing(db.get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            bank_core.ensure_account(conn, CHAT, datetime(2026, 9, 1, 10, 0))
            conn.execute(
                """
                INSERT INTO bank_daily_income (chat_id,user_id,income_date,amount_milli)
                VALUES (?,?,?,?)
                """,
                (CHAT, USER, issue_day.isoformat(), 100_000),
            )
            loan_id = bank_core.open_credit(
                conn, CHAT, USER, 20_000, 1,
                db.apply_sit_change, now=datetime(2026, 9, 10, 10, 0),
            )
            loan = conn.execute("SELECT * FROM bank_loans WHERE id=?", (loan_id,)).fetchone()
            sums = conn.execute(
                """
                SELECT SUM(amount_milli) AS total, SUM(principal_milli) AS principal,
                       COUNT(*) AS n
                FROM bank_loan_payments WHERE loan_id=?
                """,
                (loan_id,),
            ).fetchone()
            account = conn.execute(
                "SELECT liquidity_milli FROM bank_accounts WHERE chat_id=?", (CHAT,)
            ).fetchone()
            conn.commit()
        self.assertEqual(7, sums["n"])
        self.assertEqual(loan["total_milli"], sums["total"])
        self.assertEqual(20_000, sums["principal"])
        self.assertEqual(30_000, account["liquidity_milli"])

    def test_crisis_interest_is_capped_at_thirty_days(self) -> None:
        amount = 100_000
        once = bank_core.crisis_interest_milli(amount, 1_000, 30)
        reserve = bank_core.deposit_capital_reserve_milli(amount, 1_000, 1)
        normal = bank_core.weekly_interest_milli(amount, 1_000, 1)
        maturity = amount + normal
        self.assertEqual(
            normal + bank_core.crisis_interest_milli(maturity, 1_000, 30),
            reserve,
        )
        self.assertGreater(once, 0)

    def test_mature_deposit_pays_principal_and_taxed_interest(self) -> None:
        with closing(db.get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            bank_core.open_deposit(
                conn, CHAT, USER, 100_000, 1, False,
                db.apply_sit_change, now=datetime(2026, 9, 1, 10, 0),
            )
            conn.commit()
        with closing(db.get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            report = bank_core.run_daily_clearing(
                conn, CHAT, date(2026, 9, 8), db.apply_sit_change,
                now=datetime(2026, 9, 8, 23, 0),
            )
            conn.commit()
        with closing(db.get_connection()) as conn:
            balance = conn.execute(
                "SELECT sits FROM users WHERE chat_id=? AND user_id=?", (CHAT, USER)
            ).fetchone()[0]
            claim = conn.execute(
                "SELECT status FROM bank_deposit_claims"
            ).fetchone()
            account = conn.execute(
                "SELECT liquidity_milli,capital_milli FROM bank_accounts WHERE chat_id=?",
                (CHAT,),
            ).fetchone()
        self.assertEqual(202.85, balance)
        self.assertEqual("paid", claim["status"])
        self.assertEqual(103_000, report["claim_payments_milli"])
        self.assertEqual((47_150, 47_150), tuple(account))

    def test_three_open_overdues_create_one_fixed_default(self) -> None:
        self._add_message_history(date(2026, 8, 29), 20)
        with closing(db.get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            bank_core.ensure_account(conn, CHAT, datetime(2026, 9, 1, 10, 0))
            conn.execute(
                """
                INSERT INTO bank_daily_income (chat_id,user_id,income_date,amount_milli)
                VALUES (?,?,?,100000)
                """,
                (CHAT, USER, "2026-09-10"),
            )
            loan_id = bank_core.open_credit(
                conn, CHAT, USER, 20_000, 1,
                db.apply_sit_change, now=datetime(2026, 9, 10, 10, 0),
            )
            conn.execute(
                "UPDATE users SET sits=0 WHERE chat_id=? AND user_id=?", (CHAT, USER)
            )
            conn.commit()
        for run_day in (date(2026, 9, 11), date(2026, 9, 12), date(2026, 9, 13)):
            with closing(db.get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                bank_core.run_daily_clearing(
                    conn, CHAT, run_day, db.apply_sit_change,
                    now=datetime.combine(run_day, datetime.min.time()).replace(hour=23),
                )
                conn.commit()
        with closing(db.get_connection()) as conn:
            loan = conn.execute("SELECT status,total_milli FROM bank_loans WHERE id=?", (loan_id,)).fetchone()
            profile = conn.execute(
                "SELECT rating,default_debt_milli FROM bank_credit_profiles WHERE chat_id=? AND user_id=?",
                (CHAT, USER),
            ).fetchone()
        self.assertEqual("defaulted", loan["status"])
        self.assertEqual(loan["total_milli"], profile["default_debt_milli"])
        self.assertEqual(5, profile["rating"])

    def test_claim_distribution_is_proportional(self) -> None:
        with closing(db.get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            bank_core.ensure_account(conn, CHAT, datetime(2026, 9, 1, 10, 0))
            conn.execute(
                "UPDATE bank_accounts SET liquidity_milli=100000 WHERE chat_id=?", (CHAT,)
            )
            for deposit_id, user_id, amount in ((1, USER, 100_000), (2, 202, 200_000)):
                conn.execute(
                    """
                    INSERT INTO bank_deposit_claims (
                        deposit_id,chat_id,user_id,principal_remaining_milli,
                        interest_remaining_milli,rate_bp,created_date,status
                    ) VALUES (?,?,?,?,0,100,?,'open')
                    """,
                    (deposit_id, CHAT, user_id, amount, "2026-09-10"),
                )
            bank_core.run_daily_clearing(
                conn, CHAT, date(2026, 9, 10), db.apply_sit_change,
                now=datetime(2026, 9, 10, 23, 0),
            )
            claims = conn.execute(
                "SELECT user_id,principal_remaining_milli FROM bank_deposit_claims ORDER BY user_id"
            ).fetchall()
            conn.commit()
        by_user = {int(row["user_id"]): int(row["principal_remaining_milli"]) for row in claims}
        self.assertEqual(66_667, by_user[USER])
        self.assertEqual(133_333, by_user[202])

    def test_crisis_claim_stops_compounding_after_thirty_days(self) -> None:
        with closing(db.get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            bank_core.ensure_account(conn, CHAT, datetime(2026, 1, 1, 10, 0))
            conn.execute(
                "UPDATE bank_accounts SET liquidity_milli=0,capital_milli=500000 WHERE chat_id=?",
                (CHAT,),
            )
            conn.execute(
                """
                INSERT INTO bank_deposit_claims (
                    deposit_id,chat_id,user_id,principal_remaining_milli,
                    interest_remaining_milli,rate_bp,capital_reserve_remaining_milli,
                    created_date,status
                ) VALUES (1,?,?,100000,0,1000,200000,'2026-01-01','open')
                """,
                (CHAT, USER),
            )
            conn.commit()
        for offset in range(1, 33):
            run_day = date(2026, 1, 1) + timedelta(days=offset)
            with closing(db.get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                bank_core.run_daily_clearing(
                    conn, CHAT, run_day, db.apply_sit_change,
                    now=datetime.combine(run_day, datetime.min.time()).replace(hour=23),
                )
                conn.commit()
        with closing(db.get_connection()) as conn:
            claim = conn.execute(
                "SELECT crisis_interest_days,last_capitalized_date FROM bank_deposit_claims"
            ).fetchone()
        self.assertEqual(30, claim["crisis_interest_days"])
        self.assertEqual("2026-01-31", claim["last_capitalized_date"])

    def test_rate_changes_obey_daily_limits(self) -> None:
        with closing(db.get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            bank_core.change_rate(
                conn, CHAT, USER, "tax", 600, now=datetime(2026, 9, 1, 10, 0)
            )
            with self.assertRaises(ValueError):
                bank_core.change_rate(
                    conn, CHAT, USER, "tax", 700, now=datetime(2026, 9, 1, 11, 0)
                )
            conn.commit()
        with closing(db.get_connection()) as conn:
            account = conn.execute(
                "SELECT tax_rate_bp FROM bank_accounts WHERE chat_id=?", (CHAT,)
            ).fetchone()
        self.assertEqual(600, account["tax_rate_bp"])

    def test_historical_income_backfill_is_idempotent(self) -> None:
        with closing(db.get_connection()) as conn:
            conn.execute(
                "DELETE FROM bank_migrations WHERE migration_key='credit-income-from-sit-ledger-v1'"
            )
            conn.execute("DELETE FROM bank_daily_income")
            for row_id in (1, 2):
                conn.execute(
                    """
                    INSERT INTO sit_ledger (
                        created_at,date,time,chat_id,user_id,nick,display_name,
                        amount,balance_before,balance_after,action_code,action_ru,metadata_json
                    ) VALUES (?,?,?,?,?,'','',?,0,?,'quest_reward','Квест','{}')
                    """,
                    (
                        f"2026-09-01T10:00:0{row_id}", "2026-09-01", "10:00:00",
                        CHAT, USER, 2.5, 2.5,
                    ),
                )
            conn.commit()
        db.initialize_db()
        db.initialize_db()
        with closing(db.get_connection()) as conn:
            row = conn.execute(
                "SELECT amount_milli FROM bank_daily_income WHERE chat_id=? AND user_id=?",
                (CHAT, USER),
            ).fetchone()
        self.assertEqual(5_000, row["amount_milli"])


if __name__ == "__main__":
    unittest.main()
