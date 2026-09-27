"""Transactional banking primitives shared by the bot, web and database layer."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


MILLI_PER_SIT = 1_000
BASIS_POINTS = 10_000
STARTING_CAPITAL_MILLI = 50 * MILLI_PER_SIT
DEFAULT_KEY_RATE_BP = 1_000
DEFAULT_TAX_RATE_BP = 500
DEFAULT_RATING = 15
MIN_RATING = 3
MAX_RATING = 60
DEFAULT_GARNISHMENT_BP = 8_000
HISTORY_START_DATE = date(2026, 8, 29)
CRISIS_INTEREST_MAX_DAYS = 30
MIN_DEPOSIT_MILLI = 10 * MILLI_PER_SIT
MAX_DEPOSIT_MILLI = 1_000 * MILLI_PER_SIT
MIN_CREDIT_MILLI = 10 * MILLI_PER_SIT
ALLOWED_TERMS = (1, 3, 5)


SYSTEM_INCOME_ACTIONS = {
    "cepen_scratch_reward",
    "coffee_filter_reward",
    "daily_activity_award",
    "dick_throne_reward",
    "fight_club_win",
    "geyser_bonus_reward",
    "geyser_catch_reward",
    "group_event_freebie_reward",
    "group_event_win",
    "idle_buildings_income",
    "new_year_gift",
    "quest_reward",
    "shpeh_partner_reward",
    "web_geyser_catch_reward",
    "web_geyser_owner_reward",
    "web_geyser_visitor_reward",
    "bank_deposit_interest",
}
P2P_INCOME_ACTIONS = {"transfer_received", "web_transfer_received"}
EXEMPT_POSITIVE_ACTIONS = {
    "admin_charity_grant",
    "fight_club_bet_refund",
    "group_event_join_refund",
    "group_event_start_refund",
    "premium_purchase_refund",
    "shop_spider_refund",
    "bank_deposit_principal_return",
    "bank_credit_disbursement",
}


@dataclass(frozen=True)
class IncomingSplit:
    gross_milli: int
    player_milli: int
    tax_milli: int
    garnishment_milli: int
    qualifies_for_credit_income: bool


def sits_to_milli(value: Any) -> int:
    raw = Decimal(str(value).replace(",", ".")) * MILLI_PER_SIT
    return int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def milli_to_sits(value: int) -> float:
    return float((Decimal(int(value)) / MILLI_PER_SIT).quantize(Decimal("0.001")))


def mul_bp(amount_milli: int, rate_bp: int) -> int:
    raw = Decimal(int(amount_milli)) * Decimal(int(rate_bp)) / BASIS_POINTS
    return int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def weekly_interest_milli(principal_milli: int, rate_bp: int, weeks: int) -> int:
    return mul_bp(principal_milli, rate_bp * weeks)


def crisis_interest_milli(amount_milli: int, weekly_rate_bp: int, days: int = 1) -> int:
    if amount_milli <= 0 or weekly_rate_bp <= 0 or days <= 0:
        return 0
    weekly = Decimal(1) + Decimal(weekly_rate_bp) / BASIS_POINTS
    daily = weekly ** (Decimal(1) / Decimal(7)) - Decimal(1)
    raw = Decimal(amount_milli) * ((Decimal(1) + daily) ** Decimal(days) - Decimal(1))
    return int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def deposit_capital_reserve_milli(principal_milli: int, rate_bp: int, weeks: int) -> int:
    normal_interest = weekly_interest_milli(principal_milli, rate_bp, weeks)
    maturity = principal_milli + normal_interest
    return normal_interest + crisis_interest_milli(
        maturity, rate_bp, CRISIS_INTEREST_MAX_DAYS
    )


def ensure_schema(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bank_accounts (
            chat_id INTEGER PRIMARY KEY,
            liquidity_milli INTEGER NOT NULL,
            capital_milli INTEGER NOT NULL,
            key_rate_bp INTEGER NOT NULL DEFAULT 1000,
            tax_rate_bp INTEGER NOT NULL DEFAULT 500,
            last_key_rate_change_date TEXT,
            last_tax_rate_change_date TEXT,
            created_at TEXT NOT NULL,
            created_date TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bank_ministers (
            chat_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            appointed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bank_credit_profiles (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            rating INTEGER NOT NULL DEFAULT 15,
            default_debt_milli INTEGER NOT NULL DEFAULT 0,
            defaults_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS bank_daily_income (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            income_date TEXT NOT NULL,
            amount_milli INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, user_id, income_date)
        );

        CREATE TABLE IF NOT EXISTS bank_deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            principal_milli INTEGER NOT NULL,
            rate_bp INTEGER NOT NULL,
            term_weeks INTEGER NOT NULL,
            opened_at TEXT NOT NULL,
            start_date TEXT NOT NULL,
            maturity_date TEXT NOT NULL,
            maturity_milli INTEGER NOT NULL,
            capital_reserve_milli INTEGER NOT NULL,
            auto_renew INTEGER NOT NULL DEFAULT 0,
            renewal_offer_rate_bp INTEGER,
            renewal_offer_date TEXT,
            renewal_notified_at TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            closed_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_bank_deposit_one_active
        ON bank_deposits(chat_id, user_id)
        WHERE status = 'active';
        CREATE INDEX IF NOT EXISTS idx_bank_deposit_maturity
        ON bank_deposits(chat_id, status, maturity_date);

        CREATE TABLE IF NOT EXISTS bank_loans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            principal_milli INTEGER NOT NULL,
            remaining_principal_milli INTEGER NOT NULL,
            rate_bp INTEGER NOT NULL,
            term_weeks INTEGER NOT NULL,
            total_milli INTEGER NOT NULL,
            paid_milli INTEGER NOT NULL DEFAULT 0,
            issued_at TEXT NOT NULL,
            start_date TEXT NOT NULL,
            first_payment_date TEXT NOT NULL,
            raw_limit_milli INTEGER NOT NULL,
            available_limit_milli INTEGER NOT NULL,
            rating_threshold_milli INTEGER NOT NULL,
            had_overdue INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            closed_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_bank_loan_one_active
        ON bank_loans(chat_id, user_id)
        WHERE status = 'active';

        CREATE TABLE IF NOT EXISTS bank_loan_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            loan_id INTEGER NOT NULL,
            installment_no INTEGER NOT NULL,
            due_date TEXT NOT NULL,
            amount_milli INTEGER NOT NULL,
            principal_milli INTEGER NOT NULL,
            interest_milli INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'scheduled',
            paid_at TEXT,
            UNIQUE (loan_id, installment_no)
        );
        CREATE INDEX IF NOT EXISTS idx_bank_payments_due
        ON bank_loan_payments(status, due_date);

        CREATE TABLE IF NOT EXISTS bank_deposit_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deposit_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            principal_remaining_milli INTEGER NOT NULL,
            interest_remaining_milli INTEGER NOT NULL,
            rate_bp INTEGER NOT NULL,
            capital_reserve_remaining_milli INTEGER NOT NULL DEFAULT 0,
            crisis_interest_days INTEGER NOT NULL DEFAULT 0,
            created_date TEXT NOT NULL,
            last_capitalized_date TEXT,
            status TEXT NOT NULL DEFAULT 'open'
        );
        CREATE INDEX IF NOT EXISTS idx_bank_claims_open
        ON bank_deposit_claims(chat_id, status);

        CREATE TABLE IF NOT EXISTS bank_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            operation_date TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            user_id INTEGER,
            event_code TEXT NOT NULL,
            liquidity_delta_milli INTEGER NOT NULL DEFAULT 0,
            capital_delta_milli INTEGER NOT NULL DEFAULT 0,
            liquidity_after_milli INTEGER NOT NULL,
            capital_after_milli INTEGER NOT NULL,
            reference_type TEXT,
            reference_id INTEGER,
            idempotency_key TEXT UNIQUE,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_bank_ledger_chat_date
        ON bank_ledger(chat_id, operation_date);

        CREATE TABLE IF NOT EXISTS bank_daily_runs (
            chat_id INTEGER NOT NULL,
            run_date TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            report_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (chat_id, run_date)
        );

        CREATE TABLE IF NOT EXISTS bank_rate_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            changed_at TEXT NOT NULL,
            changed_date TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            rate_kind TEXT NOT NULL,
            old_bp INTEGER NOT NULL,
            new_bp INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bank_migrations (
            migration_key TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        """
    )
    claim_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(bank_deposit_claims)")
    }
    if "capital_reserve_remaining_milli" not in claim_columns:
        conn.execute(
            """
            ALTER TABLE bank_deposit_claims
            ADD COLUMN capital_reserve_remaining_milli INTEGER NOT NULL DEFAULT 0
            """
        )
    deposit_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(bank_deposits)")
    }
    if "renewal_notified_at" not in deposit_columns:
        conn.execute("ALTER TABLE bank_deposits ADD COLUMN renewal_notified_at TEXT")
    conn.execute(
        """
        INSERT OR IGNORE INTO bank_ministers (chat_id, user_id, appointed_at)
        VALUES (-1002730880821, 455180422, ?)
        """,
        (datetime.now().isoformat(timespec="seconds"),),
    )
    _backfill_historical_income(conn)


def _backfill_historical_income(conn) -> None:
    migration_key = "credit-income-from-sit-ledger-v1"
    if conn.execute(
        "SELECT 1 FROM bank_migrations WHERE migration_key=?", (migration_key,)
    ).fetchone():
        return
    action_codes = sorted(SYSTEM_INCOME_ACTIONS - {"bank_deposit_interest"})
    placeholders = ",".join("?" for _ in action_codes)
    rows = conn.execute(
        f"""
        SELECT chat_id,user_id,date,amount,action_code
        FROM sit_ledger
        WHERE date>=? AND amount>0
          AND (action_code IN ({placeholders})
               OR (action_code LIKE 'weekly_%' AND action_code LIKE '%_award'))
        """,
        (HISTORY_START_DATE.isoformat(), *action_codes),
    ).fetchall()
    totals: dict[tuple[int, int, str], int] = {}
    for row in rows:
        key = (int(row["chat_id"]), int(row["user_id"]), str(row["date"]))
        totals[key] = totals.get(key, 0) + sits_to_milli(row["amount"])
    for (chat_id, user_id, income_date), amount_milli in totals.items():
        conn.execute(
            """
            INSERT INTO bank_daily_income (chat_id,user_id,income_date,amount_milli)
            VALUES (?,?,?,?)
            ON CONFLICT(chat_id,user_id,income_date) DO NOTHING
            """,
            (chat_id, user_id, income_date, amount_milli),
        )
    conn.execute(
        "INSERT INTO bank_migrations (migration_key,applied_at) VALUES (?,?)",
        (migration_key, datetime.now().isoformat(timespec="seconds")),
    )


def ensure_account(conn, chat_id: int, now: datetime | None = None):
    row = conn.execute(
        "SELECT * FROM bank_accounts WHERE chat_id=?", (chat_id,)
    ).fetchone()
    if row is not None:
        return row
    current = now or datetime.now()
    created_at = current.isoformat(timespec="seconds")
    created_date = current.date().isoformat()
    conn.execute(
        """
        INSERT INTO bank_accounts (
            chat_id, liquidity_milli, capital_milli, key_rate_bp, tax_rate_bp,
            created_at, created_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chat_id,
            STARTING_CAPITAL_MILLI,
            STARTING_CAPITAL_MILLI,
            DEFAULT_KEY_RATE_BP,
            DEFAULT_TAX_RATE_BP,
            created_at,
            created_date,
        ),
    )
    conn.execute(
        """
        INSERT INTO bank_ledger (
            created_at, operation_date, chat_id, event_code,
            liquidity_delta_milli, capital_delta_milli,
            liquidity_after_milli, capital_after_milli,
            idempotency_key, metadata_json
        ) VALUES (?, ?, ?, 'bank_genesis', ?, ?, ?, ?, ?, ?)
        """,
        (
            created_at,
            created_date,
            chat_id,
            STARTING_CAPITAL_MILLI,
            STARTING_CAPITAL_MILLI,
            STARTING_CAPITAL_MILLI,
            STARTING_CAPITAL_MILLI,
            f"bank-genesis:{chat_id}",
            json.dumps({"authorized_one_time_emission": True}, ensure_ascii=False),
        ),
    )
    return conn.execute(
        "SELECT * FROM bank_accounts WHERE chat_id=?", (chat_id,)
    ).fetchone()


def post_bank_event(
    conn,
    chat_id: int,
    event_code: str,
    *,
    liquidity_delta_milli: int = 0,
    capital_delta_milli: int = 0,
    user_id: int | None = None,
    reference_type: str | None = None,
    reference_id: int | None = None,
    idempotency_key: str | None = None,
    metadata: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    current = now or datetime.now()
    account = ensure_account(conn, chat_id, current)
    new_liquidity = int(account["liquidity_milli"]) + int(liquidity_delta_milli)
    new_capital = int(account["capital_milli"]) + int(capital_delta_milli)
    if new_liquidity < 0:
        raise ValueError("bank liquidity cannot be negative")
    if new_capital < 0:
        raise ValueError("bank capital cannot be negative")
    if idempotency_key:
        found = conn.execute(
            "SELECT 1 FROM bank_ledger WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        if found:
            return False
    conn.execute(
        "UPDATE bank_accounts SET liquidity_milli=?, capital_milli=? WHERE chat_id=?",
        (new_liquidity, new_capital, chat_id),
    )
    conn.execute(
        """
        INSERT INTO bank_ledger (
            created_at, operation_date, chat_id, user_id, event_code,
            liquidity_delta_milli, capital_delta_milli,
            liquidity_after_milli, capital_after_milli,
            reference_type, reference_id, idempotency_key, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            current.isoformat(timespec="seconds"),
            current.date().isoformat(),
            chat_id,
            user_id,
            event_code,
            int(liquidity_delta_milli),
            int(capital_delta_milli),
            new_liquidity,
            new_capital,
            reference_type,
            reference_id,
            idempotency_key,
            json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
        ),
    )
    return True


def action_policy(action_code: str) -> tuple[bool, bool]:
    """Return (tax_and_garnish, qualifies_for_credit_income)."""
    if action_code in EXEMPT_POSITIVE_ACTIONS:
        return False, False
    if action_code in P2P_INCOME_ACTIONS:
        return True, False
    if action_code.startswith("weekly_") and action_code.endswith("_award"):
        return True, True
    if action_code in SYSTEM_INCOME_ACTIONS:
        return True, True
    return False, False


def split_incoming(
    conn,
    chat_id: int,
    user_id: int,
    gross_milli: int,
    action_code: str,
    *,
    now: datetime | None = None,
) -> IncomingSplit:
    taxable, qualifies = action_policy(action_code)
    if gross_milli <= 0 or not taxable:
        return IncomingSplit(gross_milli, gross_milli, 0, 0, False)

    current = now or datetime.now()
    account = ensure_account(conn, chat_id, current)
    tax_milli = min(gross_milli, mul_bp(gross_milli, int(account["tax_rate_bp"])))
    profile = conn.execute(
        """
        SELECT default_debt_milli FROM bank_credit_profiles
        WHERE chat_id=? AND user_id=?
        """,
        (chat_id, user_id),
    ).fetchone()
    debt_milli = int(profile["default_debt_milli"]) if profile else 0
    garnishment_target = mul_bp(gross_milli, DEFAULT_GARNISHMENT_BP)
    garnishment_milli = min(debt_milli, garnishment_target, gross_milli - tax_milli)
    player_milli = gross_milli - tax_milli - garnishment_milli

    if garnishment_milli:
        conn.execute(
            """
            UPDATE bank_credit_profiles
            SET default_debt_milli=default_debt_milli-?, updated_at=?
            WHERE chat_id=? AND user_id=?
            """,
            (
                garnishment_milli,
                current.isoformat(timespec="seconds"),
                chat_id,
                user_id,
            ),
        )
    post_bank_event(
        conn,
        chat_id,
        "income_split",
        liquidity_delta_milli=tax_milli + garnishment_milli,
        capital_delta_milli=tax_milli,
        user_id=user_id,
        metadata={
            "action_code": action_code,
            "gross_milli": gross_milli,
            "player_milli": player_milli,
            "tax_milli": tax_milli,
            "garnishment_milli": garnishment_milli,
        },
        now=current,
    )
    if qualifies and player_milli:
        conn.execute(
            """
            INSERT INTO bank_daily_income (chat_id, user_id, income_date, amount_milli)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id, income_date) DO UPDATE SET
                amount_milli=bank_daily_income.amount_milli+excluded.amount_milli
            """,
            (chat_id, user_id, current.date().isoformat(), player_milli),
        )
    return IncomingSplit(
        gross_milli,
        player_milli,
        tax_milli,
        garnishment_milli,
        qualifies,
    )


def reserved_capital_milli(conn, chat_id: int) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(capital_reserve_milli), 0) AS value
        FROM bank_deposits WHERE chat_id=? AND status='active'
        """,
        (chat_id,),
    ).fetchone()
    claim_row = conn.execute(
        """
        SELECT COALESCE(SUM(capital_reserve_remaining_milli), 0) AS value
        FROM bank_deposit_claims WHERE chat_id=? AND status='open'
        """,
        (chat_id,),
    ).fetchone()
    return int(row["value"] or 0) + int(claim_row["value"] or 0)


def bank_metrics(conn, chat_id: int, on_date: date | None = None) -> dict[str, Any]:
    account = ensure_account(conn, chat_id)
    day = on_date or datetime.now().date()
    deposits = conn.execute(
        """
        SELECT COALESCE(SUM(maturity_milli), 0) AS total
        FROM bank_deposits WHERE chat_id=? AND status='active'
        """,
        (chat_id,),
    ).fetchone()
    claims = conn.execute(
        """
        SELECT COALESCE(SUM(principal_remaining_milli+interest_remaining_milli), 0) AS total
        FROM bank_deposit_claims WHERE chat_id=? AND status='open'
        """,
        (chat_id,),
    ).fetchone()
    p2_end = (day + timedelta(days=2)).isoformat()
    near = conn.execute(
        """
        SELECT COALESCE(SUM(maturity_milli), 0) AS total
        FROM bank_deposits
        WHERE chat_id=? AND status='active' AND maturity_date<=?
        """,
        (chat_id, p2_end),
    ).fetchone()
    principal = conn.execute(
        """
        SELECT COALESCE(SUM(remaining_principal_milli), 0) AS total
        FROM bank_loans WHERE chat_id=? AND status='active'
        """,
        (chat_id,),
    ).fetchone()
    tomorrow = conn.execute(
        """
        SELECT COALESCE(SUM(maturity_milli), 0) AS total
        FROM bank_deposits
        WHERE chat_id=? AND status='active' AND maturity_date=?
        """,
        (chat_id, (day + timedelta(days=1)).isoformat()),
    ).fetchone()
    default_debt = conn.execute(
        """
        SELECT COALESCE(SUM(default_debt_milli), 0) AS total
        FROM bank_credit_profiles WHERE chat_id=?
        """,
        (chat_id,),
    ).fetchone()
    overdue = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM bank_loan_payments p
        JOIN bank_loans l ON l.id=p.loan_id
        WHERE l.chat_id=? AND p.status='overdue'
        """,
        (chat_id,),
    ).fetchone()
    deposit_obligations = int(deposits["total"] or 0) + int(claims["total"] or 0)
    p2 = int(near["total"] or 0) + int(claims["total"] or 0)
    reserve = max(mul_bp(deposit_obligations, 1_000), p2)
    liquidity = int(account["liquidity_milli"])
    capital = int(account["capital_milli"])
    free_liquidity = max(0, liquidity - reserve)
    credit_portfolio = int(principal["total"] or 0)
    utilization = (
        credit_portfolio / (credit_portfolio + free_liquidity)
        if credit_portfolio + free_liquidity
        else 0.0
    )
    coverage = math.inf if reserve == 0 else liquidity / reserve
    reserved_capital = reserved_capital_milli(conn, chat_id)
    if coverage < 1:
        state = "crisis"
    elif coverage <= 1.05:
        state = "reserve"
    elif coverage < 1.20:
        state = "deficit"
    elif coverage < 1.50:
        state = "tension"
    else:
        state = "normal"
    return {
        "liquidity_milli": liquidity,
        "capital_milli": capital,
        "reserved_capital_milli": reserved_capital,
        "free_capital_milli": capital - reserved_capital,
        "deposit_obligations_milli": deposit_obligations,
        "reserve_milli": reserve,
        "free_liquidity_milli": free_liquidity,
        "credit_portfolio_milli": credit_portfolio,
        "default_debt_milli": int(default_debt["total"] or 0),
        "overdue_payments": int(overdue["total"] or 0),
        "payout_tomorrow_milli": int(tomorrow["total"] or 0),
        "payout_two_days_milli": p2,
        "coverage": coverage,
        "utilization": utilization,
        "state": state,
        "key_rate_bp": int(account["key_rate_bp"]),
        "tax_rate_bp": int(account["tax_rate_bp"]),
    }


def market_mod_bp(utilization: float) -> int:
    if utilization <= 0.30:
        return -300
    if utilization <= 0.60:
        return -100
    if utilization <= 0.80:
        return 0
    if utilization <= 0.90:
        return 200
    return 400


def offered_rates(conn, chat_id: int) -> tuple[int, int]:
    metrics = bank_metrics(conn, chat_id)
    mod = market_mod_bp(float(metrics["utilization"]))
    key = int(metrics["key_rate_bp"])
    return max(10, key - 400 + mod), max(key, key + 600 + mod)


def credit_profile(conn, chat_id: int, user_id: int):
    ensure_account(conn, chat_id)
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT OR IGNORE INTO bank_credit_profiles (
            chat_id, user_id, rating, default_debt_milli, defaults_count, updated_at
        ) VALUES (?, ?, ?, 0, 0, ?)
        """,
        (chat_id, user_id, DEFAULT_RATING, now),
    )
    return conn.execute(
        "SELECT * FROM bank_credit_profiles WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()


def average_daily_income_milli(
    conn, chat_id: int, user_id: int, on_date: date | None = None
) -> tuple[int, int, date | None]:
    day = on_date or datetime.now().date()
    try:
        first_row = conn.execute(
            """
            SELECT MIN(date) AS first_date FROM daily_stats
            WHERE chat_id=? AND user_id=? AND messages>0
            """,
            (chat_id, user_id),
        ).fetchone()
    except Exception:
        first_row = None
    first_message = (
        date.fromisoformat(first_row["first_date"])
        if first_row and first_row["first_date"]
        else None
    )
    if first_message is None:
        return 0, 0, None
    history_start = max(first_message, HISTORY_START_DATE, day - timedelta(days=89))
    if history_start > day:
        return 0, 0, first_message
    days = (day - history_start).days + 1
    row = conn.execute(
        """
        SELECT COALESCE(SUM(amount_milli), 0) AS total
        FROM bank_daily_income
        WHERE chat_id=? AND user_id=? AND income_date BETWEEN ? AND ?
        """,
        (chat_id, user_id, history_start.isoformat(), day.isoformat()),
    ).fetchone()
    total = int(row["total"] or 0)
    average = int((Decimal(total) / days).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return average, days, first_message


def risk_mod_bp(rating: int) -> int:
    rating = max(MIN_RATING, min(MAX_RATING, int(rating)))
    if rating <= DEFAULT_RATING:
        value = Decimal(1_000) * (DEFAULT_RATING - rating) / 12
    else:
        value = Decimal(-200) * (rating - DEFAULT_RATING) / 45
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def credit_quote(conn, chat_id: int, user_id: int) -> dict[str, Any]:
    metrics = bank_metrics(conn, chat_id)
    profile = credit_profile(conn, chat_id, user_id)
    average, history_days, first_message = average_daily_income_milli(
        conn, chat_id, user_id
    )
    rating = int(profile["rating"])
    raw_limit = average * rating
    coverage = float(metrics["coverage"])
    if coverage >= 1.50:
        mode_limit = raw_limit
    elif coverage >= 1.20:
        mode_limit = mul_bp(raw_limit, 7_500)
    elif coverage > 1.05:
        mode_limit = mul_bp(raw_limit, 2_500)
    else:
        mode_limit = 0
    available = min(mode_limit, int(metrics["free_liquidity_milli"]))
    _, base_credit_rate = offered_rates(conn, chat_id)
    credit_rate = max(
        int(metrics["key_rate_bp"]), base_credit_rate + risk_mod_bp(rating)
    )
    thirty_percent = mul_bp(raw_limit, 3_000)
    threshold = (
        thirty_percent
        if thirty_percent <= available
        else mul_bp(available, 9_000)
    )
    eligible_date = first_message + timedelta(days=7) if first_message else None
    history_eligible = bool(eligible_date and datetime.now().date() >= eligible_date)
    return {
        "rating": rating,
        "default_debt_milli": int(profile["default_debt_milli"]),
        "average_income_milli": average,
        "history_days": history_days,
        "first_message_date": first_message,
        "history_eligible": history_eligible,
        "raw_limit_milli": raw_limit,
        "available_limit_milli": max(0, available),
        "rating_threshold_milli": max(0, threshold),
        "credit_rate_bp": credit_rate,
    }


def _banking_start_date(current: datetime) -> date:
    return current.date() + timedelta(days=1) if current.hour >= 23 else current.date()


def _active_deposit(conn, chat_id: int, user_id: int):
    return conn.execute(
        """
        SELECT * FROM bank_deposits
        WHERE chat_id=? AND user_id=? AND status='active'
        """,
        (chat_id, user_id),
    ).fetchone()


def _active_loan(conn, chat_id: int, user_id: int):
    return conn.execute(
        """
        SELECT * FROM bank_loans
        WHERE chat_id=? AND user_id=? AND status='active'
        """,
        (chat_id, user_id),
    ).fetchone()


def open_deposit(
    conn,
    chat_id: int,
    user_id: int,
    amount_milli: int,
    term_weeks: int,
    auto_renew: bool,
    apply_change,
    *,
    now: datetime | None = None,
    expected_rate_bp: int | None = None,
) -> int:
    current = now or datetime.now()
    ensure_account(conn, chat_id, current)
    if term_weeks not in ALLOWED_TERMS:
        raise ValueError("unsupported deposit term")
    if not MIN_DEPOSIT_MILLI <= amount_milli <= MAX_DEPOSIT_MILLI:
        raise ValueError("deposit amount is outside allowed range")
    if _active_deposit(conn, chat_id, user_id):
        raise ValueError("user already has an active deposit")
    metrics = bank_metrics(conn, chat_id, current.date())
    if metrics["state"] == "crisis":
        raise ValueError("bank is in cash crisis")
    deposit_rate, _ = offered_rates(conn, chat_id)
    if expected_rate_bp is not None and deposit_rate != int(expected_rate_bp):
        raise ValueError("deposit conditions changed; request a new quote")
    interest = weekly_interest_milli(amount_milli, deposit_rate, term_weeks)
    maturity = amount_milli + interest
    capital_reserve = deposit_capital_reserve_milli(
        amount_milli, deposit_rate, term_weeks
    )
    if capital_reserve > int(metrics["free_capital_milli"]):
        raise ValueError("bank has insufficient free capital for deposit interest")
    apply_change(
        conn,
        chat_id,
        user_id,
        -milli_to_sits(amount_milli),
        action_code="bank_deposit_open",
        action_ru="Открытие банковского вклада",
        metadata={"term_weeks": term_weeks, "auto_renew": bool(auto_renew)},
        require_sufficient=True,
    )
    post_bank_event(
        conn,
        chat_id,
        "deposit_open",
        liquidity_delta_milli=amount_milli,
        user_id=user_id,
        metadata={
            "principal_milli": amount_milli,
            "rate_bp": deposit_rate,
            "term_weeks": term_weeks,
        },
        now=current,
    )
    start = _banking_start_date(current)
    maturity_date = start + timedelta(days=7 * term_weeks)
    cur = conn.execute(
        """
        INSERT INTO bank_deposits (
            chat_id, user_id, principal_milli, rate_bp, term_weeks,
            opened_at, start_date, maturity_date, maturity_milli,
            capital_reserve_milli, auto_renew, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
        """,
        (
            chat_id,
            user_id,
            amount_milli,
            deposit_rate,
            term_weeks,
            current.isoformat(timespec="seconds"),
            start.isoformat(),
            maturity_date.isoformat(),
            maturity,
            capital_reserve,
            int(bool(auto_renew)),
        ),
    )
    deposit_id = int(cur.lastrowid)
    conn.execute(
        """
        UPDATE bank_ledger SET reference_type='deposit', reference_id=?
        WHERE id=(SELECT MAX(id) FROM bank_ledger WHERE chat_id=?)
        """,
        (deposit_id, chat_id),
    )
    return deposit_id


def set_deposit_auto_renew(
    conn, chat_id: int, user_id: int, enabled: bool
) -> bool:
    cur = conn.execute(
        """
        UPDATE bank_deposits SET auto_renew=?
        WHERE chat_id=? AND user_id=? AND status='active'
        """,
        (int(bool(enabled)), chat_id, user_id),
    )
    return cur.rowcount == 1


def close_deposit_early(
    conn,
    chat_id: int,
    user_id: int,
    apply_change,
    *,
    now: datetime | None = None,
) -> int:
    current = now or datetime.now()
    deposit = _active_deposit(conn, chat_id, user_id)
    if not deposit:
        raise ValueError("active deposit not found")
    metrics = bank_metrics(conn, chat_id, current.date())
    if metrics["state"] == "crisis":
        raise ValueError("early withdrawal is unavailable during cash crisis")
    opened = datetime.fromisoformat(str(deposit["opened_at"]))
    elapsed_days = max(Decimal(0), Decimal(str((current - opened).total_seconds())) / 86_400)
    raw_interest = (
        Decimal(int(deposit["principal_milli"]))
        * Decimal(10)
        / BASIS_POINTS
        * elapsed_days
        / Decimal(7)
    )
    interest = int(raw_interest.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    gross = int(deposit["principal_milli"]) + interest
    if gross > int(metrics["liquidity_milli"]):
        raise ValueError("bank has insufficient liquidity for early withdrawal")
    post_bank_event(
        conn,
        chat_id,
        "deposit_early_close",
        liquidity_delta_milli=-gross,
        capital_delta_milli=-interest,
        user_id=user_id,
        reference_type="deposit",
        reference_id=int(deposit["id"]),
        metadata={"principal_milli": int(deposit["principal_milli"]), "interest_milli": interest},
        now=current,
    )
    apply_change(
        conn,
        chat_id,
        user_id,
        milli_to_sits(int(deposit["principal_milli"])),
        action_code="bank_deposit_principal_return",
        action_ru="Досрочный возврат тела банковского вклада",
        metadata={"deposit_id": int(deposit["id"])},
    )
    if interest:
        apply_change(
            conn,
            chat_id,
            user_id,
            milli_to_sits(interest),
            action_code="bank_deposit_interest",
            action_ru="Доход при досрочном закрытии банковского вклада",
            metadata={"deposit_id": int(deposit["id"]), "early": True},
        )
    conn.execute(
        "UPDATE bank_deposits SET status='closed_early', closed_at=? WHERE id=?",
        (current.isoformat(timespec="seconds"), int(deposit["id"])),
    )
    return gross


def _installment_schedule(
    principal_milli: int, interest_milli: int, count: int
) -> list[tuple[int, int, int]]:
    total = principal_milli + interest_milli
    regular_total = int(
        (Decimal(total) / count).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    regular_principal = int(
        (Decimal(principal_milli) / count).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    result: list[tuple[int, int, int]] = []
    used_total = 0
    used_principal = 0
    for index in range(count):
        if index == count - 1:
            payment = total - used_total
            principal_part = principal_milli - used_principal
        else:
            payment = regular_total
            principal_part = min(regular_principal, principal_milli - used_principal)
        interest_part = payment - principal_part
        result.append((payment, principal_part, interest_part))
        used_total += payment
        used_principal += principal_part
    return result


def open_credit(
    conn,
    chat_id: int,
    user_id: int,
    amount_milli: int,
    term_weeks: int,
    apply_change,
    *,
    now: datetime | None = None,
    expected_rate_bp: int | None = None,
) -> int:
    current = now or datetime.now()
    if term_weeks not in ALLOWED_TERMS:
        raise ValueError("unsupported credit term")
    if amount_milli < MIN_CREDIT_MILLI:
        raise ValueError("credit is below minimum")
    if _active_loan(conn, chat_id, user_id):
        raise ValueError("user already has an active credit")
    quote = credit_quote(conn, chat_id, user_id)
    if expected_rate_bp is not None and int(quote["credit_rate_bp"]) != int(expected_rate_bp):
        raise ValueError("credit conditions changed; request a new quote")
    if not quote["history_eligible"]:
        raise ValueError("insufficient message history")
    if quote["default_debt_milli"] > 0:
        raise ValueError("default debt must be repaid first")
    if amount_milli > int(quote["available_limit_milli"]):
        raise ValueError("credit exceeds available limit")
    metrics = bank_metrics(conn, chat_id, current.date())
    if metrics["state"] in {"reserve", "crisis"}:
        raise ValueError("new credits are unavailable in current bank state")
    if int(metrics["free_capital_milli"]) < 0:
        raise ValueError("bank capital is fully reserved")
    rate_bp = int(quote["credit_rate_bp"])
    interest = weekly_interest_milli(amount_milli, rate_bp, term_weeks)
    total = amount_milli + interest
    start = _banking_start_date(current)
    first_payment = start + timedelta(days=term_weeks)
    cur = conn.execute(
        """
        INSERT INTO bank_loans (
            chat_id, user_id, principal_milli, remaining_principal_milli,
            rate_bp, term_weeks, total_milli, issued_at, start_date,
            first_payment_date, raw_limit_milli, available_limit_milli,
            rating_threshold_milli, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
        """,
        (
            chat_id,
            user_id,
            amount_milli,
            amount_milli,
            rate_bp,
            term_weeks,
            total,
            current.isoformat(timespec="seconds"),
            start.isoformat(),
            first_payment.isoformat(),
            int(quote["raw_limit_milli"]),
            int(quote["available_limit_milli"]),
            int(quote["rating_threshold_milli"]),
        ),
    )
    loan_id = int(cur.lastrowid)
    for index, (payment, principal_part, interest_part) in enumerate(
        _installment_schedule(amount_milli, interest, 7 * term_weeks), start=1
    ):
        conn.execute(
            """
            INSERT INTO bank_loan_payments (
                loan_id, installment_no, due_date, amount_milli,
                principal_milli, interest_milli, status
            ) VALUES (?, ?, ?, ?, ?, ?, 'scheduled')
            """,
            (
                loan_id,
                index,
                (first_payment + timedelta(days=index - 1)).isoformat(),
                payment,
                principal_part,
                interest_part,
            ),
        )
    post_bank_event(
        conn,
        chat_id,
        "credit_disbursement",
        liquidity_delta_milli=-amount_milli,
        user_id=user_id,
        reference_type="loan",
        reference_id=loan_id,
        metadata={"principal_milli": amount_milli, "rate_bp": rate_bp},
        now=current,
    )
    apply_change(
        conn,
        chat_id,
        user_id,
        milli_to_sits(amount_milli),
        action_code="bank_credit_disbursement",
        action_ru="Выдача банковского кредита",
        metadata={"loan_id": loan_id},
    )
    return loan_id


def _update_rating(conn, chat_id: int, user_id: int, delta: int, now: datetime) -> None:
    profile = credit_profile(conn, chat_id, user_id)
    rating = max(MIN_RATING, min(MAX_RATING, int(profile["rating"]) + int(delta)))
    conn.execute(
        """
        UPDATE bank_credit_profiles SET rating=?, updated_at=?
        WHERE chat_id=? AND user_id=?
        """,
        (rating, now.isoformat(timespec="seconds"), chat_id, user_id),
    )


def _finish_paid_loan(conn, loan, now: datetime, early: bool) -> None:
    qualifies = int(loan["principal_milli"]) >= int(loan["rating_threshold_milli"])
    bonus = 0
    if qualifies and not int(loan["had_overdue"]):
        term = int(loan["term_weeks"])
        if early:
            first_payment = date.fromisoformat(str(loan["first_payment_date"]))
            payment_period = 7 * term
            elapsed = max(0, (now.date() - first_payment).days + 1)
            if elapsed * 2 >= payment_period:
                bonus = {1: 1, 3: 1, 5: 2}[term]
        else:
            bonus = {1: 1, 3: 2, 5: 3}[term]
    if bonus:
        _update_rating(conn, int(loan["chat_id"]), int(loan["user_id"]), bonus, now)
    conn.execute(
        """
        UPDATE bank_loans SET status=?, remaining_principal_milli=0, closed_at=?
        WHERE id=?
        """,
        (
            "paid_early" if early else "paid",
            now.isoformat(timespec="seconds"),
            int(loan["id"]),
        ),
    )


def _pay_installment(conn, loan, payment, apply_change, now: datetime) -> None:
    amount = int(payment["amount_milli"])
    apply_change(
        conn,
        int(loan["chat_id"]),
        int(loan["user_id"]),
        -milli_to_sits(amount),
        action_code="bank_credit_payment",
        action_ru="Платёж по банковскому кредиту",
        metadata={"loan_id": int(loan["id"]), "installment_no": int(payment["installment_no"])},
        require_sufficient=True,
    )
    post_bank_event(
        conn,
        int(loan["chat_id"]),
        "credit_payment",
        liquidity_delta_milli=amount,
        capital_delta_milli=int(payment["interest_milli"]),
        user_id=int(loan["user_id"]),
        reference_type="loan",
        reference_id=int(loan["id"]),
        metadata={"installment_no": int(payment["installment_no"])},
        now=now,
    )
    conn.execute(
        "UPDATE bank_loan_payments SET status='paid', paid_at=? WHERE id=?",
        (now.isoformat(timespec="seconds"), int(payment["id"])),
    )
    conn.execute(
        """
        UPDATE bank_loans SET
            paid_milli=paid_milli+?,
            remaining_principal_milli=MAX(0, remaining_principal_milli-?)
        WHERE id=?
        """,
        (amount, int(payment["principal_milli"]), int(loan["id"])),
    )


def repay_overdue(
    conn,
    chat_id: int,
    user_id: int,
    apply_change,
    *,
    count: int | None = None,
    now: datetime | None = None,
) -> int:
    current = now or datetime.now()
    loan = _active_loan(conn, chat_id, user_id)
    if not loan:
        raise ValueError("active credit not found")
    query = """
        SELECT * FROM bank_loan_payments
        WHERE loan_id=? AND status='overdue'
        ORDER BY installment_no
    """
    params: list[Any] = [int(loan["id"])]
    if count is not None:
        query += " LIMIT ?"
        params.append(max(1, int(count)))
    payments = conn.execute(query, params).fetchall()
    if not payments:
        raise ValueError("overdue payments not found")
    paid = 0
    for payment in payments:
        _pay_installment(conn, loan, payment, apply_change, current)
        paid += int(payment["amount_milli"])
        loan = conn.execute("SELECT * FROM bank_loans WHERE id=?", (int(loan["id"]),)).fetchone()
    remaining = conn.execute(
        "SELECT COUNT(*) AS n FROM bank_loan_payments WHERE loan_id=? AND status!='paid'",
        (int(loan["id"]),),
    ).fetchone()
    if int(remaining["n"]) == 0:
        _finish_paid_loan(conn, loan, current, early=False)
    return paid


def repay_credit_early(
    conn,
    chat_id: int,
    user_id: int,
    apply_change,
    *,
    now: datetime | None = None,
) -> int:
    current = now or datetime.now()
    loan = _active_loan(conn, chat_id, user_id)
    if not loan:
        raise ValueError("active credit not found")
    issued = datetime.fromisoformat(str(loan["issued_at"]))
    elapsed_days = max(Decimal(0), Decimal(str((current - issued).total_seconds())) / 86_400)
    used_weeks = min(elapsed_days / Decimal(7), Decimal(int(loan["term_weeks"])))
    accrued = (
        Decimal(int(loan["principal_milli"]))
        * Decimal(int(loan["rate_bp"]))
        / BASIS_POINTS
        * used_weeks
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    payoff = max(
        0,
        int(loan["principal_milli"]) + int(accrued) - int(loan["paid_milli"]),
    )
    if payoff == 0:
        _finish_paid_loan(conn, loan, current, early=True)
        return 0
    apply_change(
        conn,
        chat_id,
        user_id,
        -milli_to_sits(payoff),
        action_code="bank_credit_early_payment",
        action_ru="Досрочное погашение банковского кредита",
        metadata={"loan_id": int(loan["id"])},
        require_sufficient=True,
    )
    principal_part = min(payoff, int(loan["remaining_principal_milli"]))
    interest_part = max(0, payoff - principal_part)
    post_bank_event(
        conn,
        chat_id,
        "credit_early_payment",
        liquidity_delta_milli=payoff,
        capital_delta_milli=interest_part,
        user_id=user_id,
        reference_type="loan",
        reference_id=int(loan["id"]),
        now=current,
    )
    conn.execute(
        """
        UPDATE bank_loans SET paid_milli=paid_milli+?, remaining_principal_milli=0
        WHERE id=?
        """,
        (payoff, int(loan["id"])),
    )
    conn.execute(
        """
        UPDATE bank_loan_payments SET status='cancelled'
        WHERE loan_id=? AND status!='paid'
        """,
        (int(loan["id"]),),
    )
    refreshed = conn.execute("SELECT * FROM bank_loans WHERE id=?", (int(loan["id"]),)).fetchone()
    _finish_paid_loan(conn, refreshed, current, early=True)
    return payoff


def prepare_renewal_offers(conn, chat_id: int, offer_date: date) -> list[dict[str, Any]]:
    deposit_rate, _ = offered_rates(conn, chat_id)
    tax_rate = int(bank_metrics(conn, chat_id, offer_date)["tax_rate_bp"])
    rows = conn.execute(
        """
        SELECT * FROM bank_deposits
        WHERE chat_id=? AND status='active' AND maturity_date=? AND auto_renew=1
          AND renewal_notified_at IS NULL
        """,
        (chat_id, offer_date.isoformat()),
    ).fetchall()
    offers: list[dict[str, Any]] = []
    for row in rows:
        fixed_rate = (
            int(row["renewal_offer_rate_bp"])
            if row["renewal_offer_date"] == offer_date.isoformat()
            and row["renewal_offer_rate_bp"] is not None
            else deposit_rate
        )
        conn.execute(
            """
            UPDATE bank_deposits
            SET renewal_offer_rate_bp=?, renewal_offer_date=?
            WHERE id=?
            """,
            (fixed_rate, offer_date.isoformat(), int(row["id"])),
        )
        interest = weekly_interest_milli(
            int(row["principal_milli"]), fixed_rate, int(row["term_weeks"])
        )
        offers.append(
            {
                "deposit_id": int(row["id"]),
                "user_id": int(row["user_id"]),
                "principal_milli": int(row["principal_milli"]),
                "term_weeks": int(row["term_weeks"]),
                "rate_bp": fixed_rate,
                "expected_interest_milli": interest,
                "expected_net_interest_milli": interest - mul_bp(interest, tax_rate),
            }
        )
    return offers


def mark_renewal_notified(conn, deposit_id: int, now: datetime | None = None) -> None:
    current = now or datetime.now()
    conn.execute(
        "UPDATE bank_deposits SET renewal_notified_at=? WHERE id=?",
        (current.isoformat(timespec="seconds"), deposit_id),
    )


def _create_deposit_claim(
    conn,
    deposit,
    principal_milli: int,
    interest_milli: int,
    run_date: date,
    now: datetime,
) -> int:
    if interest_milli > int(
        conn.execute(
            "SELECT capital_milli FROM bank_accounts WHERE chat_id=?",
            (int(deposit["chat_id"]),),
        ).fetchone()["capital_milli"]
    ):
        raise ValueError("deposit interest would make bank capital negative")
    post_bank_event(
        conn,
        int(deposit["chat_id"]),
        "deposit_interest_accrual",
        capital_delta_milli=-interest_milli,
        user_id=int(deposit["user_id"]),
        reference_type="deposit",
        reference_id=int(deposit["id"]),
        idempotency_key=f"deposit-interest-accrual:{int(deposit['id'])}",
        metadata={"interest_milli": interest_milli},
        now=now,
    )
    cur = conn.execute(
        """
        INSERT INTO bank_deposit_claims (
            deposit_id, chat_id, user_id, principal_remaining_milli,
            interest_remaining_milli, rate_bp,
            capital_reserve_remaining_milli, created_date, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')
        """,
        (
            int(deposit["id"]),
            int(deposit["chat_id"]),
            int(deposit["user_id"]),
            principal_milli,
            interest_milli,
            int(deposit["rate_bp"]),
            crisis_interest_milli(
                principal_milli + interest_milli,
                int(deposit["rate_bp"]),
                CRISIS_INTEREST_MAX_DAYS,
            ),
            run_date.isoformat(),
        ),
    )
    return int(cur.lastrowid)


def _renew_deposit(conn, deposit, run_date: date, now: datetime) -> bool:
    chat_id = int(deposit["chat_id"])
    metrics = bank_metrics(conn, chat_id, run_date)
    if metrics["state"] == "crisis":
        return False
    rate = (
        int(deposit["renewal_offer_rate_bp"])
        if deposit["renewal_offer_date"] == run_date.isoformat()
        and deposit["renewal_offer_rate_bp"] is not None
        else offered_rates(conn, chat_id)[0]
    )
    principal = int(deposit["principal_milli"])
    term = int(deposit["term_weeks"])
    interest = weekly_interest_milli(principal, rate, term)
    new_reserve = deposit_capital_reserve_milli(principal, rate, term)
    old_interest = int(deposit["maturity_milli"]) - principal
    claim_reserve = crisis_interest_milli(
        old_interest,
        int(deposit["rate_bp"]),
        CRISIS_INTEREST_MAX_DAYS,
    )
    reserved_without_old = max(
        0,
        int(metrics["reserved_capital_milli"])
        - int(deposit["capital_reserve_milli"]),
    )
    if reserved_without_old + claim_reserve + new_reserve > int(
        metrics["capital_milli"]
    ) - old_interest:
        return False
    _create_deposit_claim(conn, deposit, 0, old_interest, run_date, now)
    conn.execute(
        "UPDATE bank_deposits SET status='renewed', closed_at=? WHERE id=?",
        (now.isoformat(timespec="seconds"), int(deposit["id"])),
    )
    new_maturity = run_date + timedelta(days=7 * term)
    conn.execute(
        """
        INSERT INTO bank_deposits (
            chat_id, user_id, principal_milli, rate_bp, term_weeks,
            opened_at, start_date, maturity_date, maturity_milli,
            capital_reserve_milli, auto_renew, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'active')
        """,
        (
            chat_id,
            int(deposit["user_id"]),
            principal,
            rate,
            term,
            now.isoformat(timespec="seconds"),
            run_date.isoformat(),
            new_maturity.isoformat(),
            principal + interest,
            new_reserve,
        ),
    )
    return True


def _capitalize_claims(conn, chat_id: int, run_date: date, now: datetime) -> int:
    rows = conn.execute(
        """
        SELECT * FROM bank_deposit_claims
        WHERE chat_id=? AND status='open' AND created_date<?
          AND crisis_interest_days<?
          AND (last_capitalized_date IS NULL OR last_capitalized_date<?)
        """,
        (
            chat_id,
            run_date.isoformat(),
            CRISIS_INTEREST_MAX_DAYS,
            run_date.isoformat(),
        ),
    ).fetchall()
    total = 0
    for claim in rows:
        balance = int(claim["principal_remaining_milli"]) + int(
            claim["interest_remaining_milli"]
        )
        interest = crisis_interest_milli(balance, int(claim["rate_bp"]), 1)
        if interest:
            post_bank_event(
                conn,
                chat_id,
                "deposit_crisis_interest_accrual",
                capital_delta_milli=-interest,
                user_id=int(claim["user_id"]),
                reference_type="claim",
                reference_id=int(claim["id"]),
                idempotency_key=f"claim-interest:{int(claim['id'])}:{run_date.isoformat()}",
                metadata={"interest_milli": interest},
                now=now,
            )
        conn.execute(
            """
            UPDATE bank_deposit_claims SET
                interest_remaining_milli=interest_remaining_milli+?,
                capital_reserve_remaining_milli=MAX(0, capital_reserve_remaining_milli-?),
                crisis_interest_days=crisis_interest_days+1,
                last_capitalized_date=?
            WHERE id=?
            """,
            (interest, interest, run_date.isoformat(), int(claim["id"])),
        )
        total += interest
    return total


def _pay_claim(conn, claim, gross_milli: int, apply_change, now: datetime) -> None:
    principal_remaining = int(claim["principal_remaining_milli"])
    interest_remaining = int(claim["interest_remaining_milli"])
    total_remaining = principal_remaining + interest_remaining
    gross_milli = min(gross_milli, total_remaining)
    if gross_milli <= 0:
        return
    if gross_milli == total_remaining:
        principal_part = principal_remaining
    else:
        principal_part = int(
            (
                Decimal(gross_milli)
                * Decimal(principal_remaining)
                / Decimal(total_remaining)
            ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        principal_part = min(principal_part, principal_remaining, gross_milli)
    interest_part = gross_milli - principal_part
    post_bank_event(
        conn,
        int(claim["chat_id"]),
        "deposit_claim_payment",
        liquidity_delta_milli=-gross_milli,
        user_id=int(claim["user_id"]),
        reference_type="claim",
        reference_id=int(claim["id"]),
        metadata={"principal_milli": principal_part, "interest_milli": interest_part},
        now=now,
    )
    if principal_part:
        apply_change(
            conn,
            int(claim["chat_id"]),
            int(claim["user_id"]),
            milli_to_sits(principal_part),
            action_code="bank_deposit_principal_return",
            action_ru="Возврат тела банковского вклада",
            metadata={"claim_id": int(claim["id"])},
        )
    if interest_part:
        apply_change(
            conn,
            int(claim["chat_id"]),
            int(claim["user_id"]),
            milli_to_sits(interest_part),
            action_code="bank_deposit_interest",
            action_ru="Доход по банковскому вкладу",
            metadata={"claim_id": int(claim["id"])},
        )
    new_principal = principal_remaining - principal_part
    new_interest = interest_remaining - interest_part
    remaining_days = max(
        0, CRISIS_INTEREST_MAX_DAYS - int(claim["crisis_interest_days"])
    )
    reserve_remaining = crisis_interest_milli(
        new_principal + new_interest,
        int(claim["rate_bp"]),
        remaining_days,
    )
    conn.execute(
        """
        UPDATE bank_deposit_claims SET
            principal_remaining_milli=?, interest_remaining_milli=?,
            capital_reserve_remaining_milli=?,
            status=CASE WHEN ?=0 THEN 'paid' ELSE status END
        WHERE id=?
        """,
        (
            new_principal,
            new_interest,
            reserve_remaining,
            new_principal + new_interest,
            int(claim["id"]),
        ),
    )


def _distribute_claims(conn, chat_id: int, apply_change, now: datetime) -> int:
    claims = conn.execute(
        """
        SELECT * FROM bank_deposit_claims
        WHERE chat_id=? AND status='open'
        ORDER BY id
        """,
        (chat_id,),
    ).fetchall()
    if not claims:
        return 0
    account = ensure_account(conn, chat_id, now)
    available = int(account["liquidity_milli"])
    total_due = sum(
        int(row["principal_remaining_milli"]) + int(row["interest_remaining_milli"])
        for row in claims
    )
    pool = min(available, total_due)
    if pool <= 0:
        return 0
    allocations: list[int] = []
    used = 0
    for index, claim in enumerate(claims):
        claim_total = int(claim["principal_remaining_milli"]) + int(
            claim["interest_remaining_milli"]
        )
        if index == len(claims) - 1:
            allocation = min(pool - used, claim_total)
        else:
            allocation = int(
                (
                    Decimal(pool) * Decimal(claim_total) / Decimal(total_due)
                ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
            allocation = min(allocation, claim_total, pool - used)
        allocations.append(max(0, allocation))
        used += max(0, allocation)
    if used < pool:
        for index, claim in enumerate(claims):
            capacity = (
                int(claim["principal_remaining_milli"])
                + int(claim["interest_remaining_milli"])
                - allocations[index]
            )
            addition = min(capacity, pool - used)
            allocations[index] += addition
            used += addition
            if used == pool:
                break
    for claim, allocation in zip(claims, allocations):
        _pay_claim(conn, claim, allocation, apply_change, now)
    return used


def _default_loan(conn, loan, run_date: date, now: datetime) -> int:
    unpaid = conn.execute(
        """
        SELECT COALESCE(SUM(amount_milli), 0) AS total
        FROM bank_loan_payments WHERE loan_id=? AND status!='paid'
        """,
        (int(loan["id"]),),
    ).fetchone()
    debt = int(unpaid["total"] or 0)
    open_overdue = conn.execute(
        """
        SELECT COUNT(*) AS n FROM bank_loan_payments
        WHERE loan_id=? AND status='overdue'
        """,
        (int(loan["id"]),),
    ).fetchone()
    profile = credit_profile(conn, int(loan["chat_id"]), int(loan["user_id"]))
    conn.execute(
        """
        UPDATE bank_credit_profiles SET
            default_debt_milli=default_debt_milli+?,
            defaults_count=defaults_count+1,
            updated_at=?
        WHERE chat_id=? AND user_id=?
        """,
        (
            debt,
            now.isoformat(timespec="seconds"),
            int(loan["chat_id"]),
            int(loan["user_id"]),
        ),
    )
    _update_rating(
        conn,
        int(loan["chat_id"]),
        int(loan["user_id"]),
        int(open_overdue["n"]) - 10,
        now,
    )
    conn.execute(
        "UPDATE bank_loans SET status='defaulted', closed_at=? WHERE id=?",
        (now.isoformat(timespec="seconds"), int(loan["id"])),
    )
    conn.execute(
        """
        UPDATE bank_loan_payments SET status='defaulted'
        WHERE loan_id=? AND status!='paid'
        """,
        (int(loan["id"]),),
    )
    return debt


def run_daily_clearing(
    conn,
    chat_id: int,
    run_date: date,
    apply_change,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.combine(run_date, datetime.min.time()).replace(hour=23)
    ensure_account(conn, chat_id, current)
    existing = conn.execute(
        "SELECT report_json FROM bank_daily_runs WHERE chat_id=? AND run_date=?",
        (chat_id, run_date.isoformat()),
    ).fetchone()
    if existing:
        return json.loads(existing["report_json"])

    crisis_interest = _capitalize_claims(conn, chat_id, run_date, current)
    paid_credit = 0
    new_overdue = 0
    new_defaults = 0
    due_payments = conn.execute(
        """
        SELECT p.*, l.chat_id, l.user_id
        FROM bank_loan_payments p
        JOIN bank_loans l ON l.id=p.loan_id
        WHERE l.chat_id=? AND l.status='active'
          AND p.status='scheduled' AND p.due_date=?
        ORDER BY p.id
        """,
        (chat_id, run_date.isoformat()),
    ).fetchall()
    for payment in due_payments:
        loan = conn.execute(
            "SELECT * FROM bank_loans WHERE id=?", (int(payment["loan_id"]),)
        ).fetchone()
        user = conn.execute(
            "SELECT COALESCE(sits,0) AS sits FROM users WHERE chat_id=? AND user_id=?",
            (chat_id, int(loan["user_id"])),
        ).fetchone()
        balance_milli = sits_to_milli(user["sits"] if user else 0)
        if balance_milli >= int(payment["amount_milli"]):
            _pay_installment(conn, loan, payment, apply_change, current)
            paid_credit += int(payment["amount_milli"])
            remaining = conn.execute(
                """
                SELECT COUNT(*) AS n FROM bank_loan_payments
                WHERE loan_id=? AND status!='paid'
                """,
                (int(loan["id"]),),
            ).fetchone()
            if int(remaining["n"]) == 0:
                refreshed = conn.execute(
                    "SELECT * FROM bank_loans WHERE id=?", (int(loan["id"]),)
                ).fetchone()
                _finish_paid_loan(conn, refreshed, current, early=False)
        else:
            conn.execute(
                "UPDATE bank_loan_payments SET status='overdue' WHERE id=?",
                (int(payment["id"]),),
            )
            conn.execute(
                "UPDATE bank_loans SET had_overdue=1 WHERE id=?", (int(loan["id"]),)
            )
            _update_rating(conn, chat_id, int(loan["user_id"]), -1, current)
            new_overdue += 1
            count = conn.execute(
                """
                SELECT COUNT(*) AS n FROM bank_loan_payments
                WHERE loan_id=? AND status='overdue'
                """,
                (int(loan["id"]),),
            ).fetchone()
            if int(count["n"]) >= 3:
                _default_loan(conn, loan, run_date, current)
                new_defaults += 1

    matured = conn.execute(
        """
        SELECT * FROM bank_deposits
        WHERE chat_id=? AND status='active' AND maturity_date<=?
        ORDER BY id
        """,
        (chat_id, run_date.isoformat()),
    ).fetchall()
    renewed = 0
    for deposit in matured:
        if int(deposit["auto_renew"]) and _renew_deposit(conn, deposit, run_date, current):
            renewed += 1
            continue
        principal = int(deposit["principal_milli"])
        interest = int(deposit["maturity_milli"]) - principal
        _create_deposit_claim(conn, deposit, principal, interest, run_date, current)
        conn.execute(
            "UPDATE bank_deposits SET status='matured', closed_at=? WHERE id=?",
            (current.isoformat(timespec="seconds"), int(deposit["id"])),
        )

    claim_payments = _distribute_claims(conn, chat_id, apply_change, current)
    metrics = bank_metrics(conn, chat_id, run_date)
    day_flows = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN event_code='income_split' THEN capital_delta_milli ELSE 0 END),0) AS tax,
            COALESCE(SUM(CASE WHEN event_code IN ('credit_payment','credit_early_payment') THEN capital_delta_milli ELSE 0 END),0) AS credit_interest,
            COALESCE(SUM(CASE WHEN event_code IN ('deposit_interest_accrual','deposit_crisis_interest_accrual') THEN -capital_delta_milli ELSE 0 END),0) AS deposit_interest
        FROM bank_ledger WHERE chat_id=? AND operation_date=?
        """,
        (chat_id, run_date.isoformat()),
    ).fetchone()
    report = {
        "chat_id": chat_id,
        "run_date": run_date.isoformat(),
        "credit_payments_milli": paid_credit,
        "new_overdue": new_overdue,
        "new_defaults": new_defaults,
        "matured_deposits": len(matured),
        "renewed_deposits": renewed,
        "claim_payments_milli": claim_payments,
        "crisis_interest_milli": crisis_interest,
        "tax_income_milli": int(day_flows["tax"] or 0),
        "credit_interest_income_milli": int(day_flows["credit_interest"] or 0),
        "deposit_interest_expense_milli": int(day_flows["deposit_interest"] or 0),
        "metrics": {
            key: (None if isinstance(value, float) and math.isinf(value) else value)
            for key, value in metrics.items()
        },
    }
    conn.execute(
        """
        INSERT INTO bank_daily_runs (chat_id, run_date, completed_at, report_json)
        VALUES (?, ?, ?, ?)
        """,
        (
            chat_id,
            run_date.isoformat(),
            current.isoformat(timespec="seconds"),
            json.dumps(report, ensure_ascii=False, sort_keys=True),
        ),
    )
    return report


def change_rate(
    conn,
    chat_id: int,
    user_id: int,
    rate_kind: str,
    new_bp: int,
    *,
    now: datetime | None = None,
) -> None:
    current = now or datetime.now()
    account = ensure_account(conn, chat_id, current)
    if rate_kind == "key":
        column = "key_rate_bp"
        last_column = "last_key_rate_change_date"
        minimum, maximum, max_delta = 0, 3_000, 500
    elif rate_kind == "tax":
        column = "tax_rate_bp"
        last_column = "last_tax_rate_change_date"
        minimum, maximum, max_delta = 0, 2_000, 100
    else:
        raise ValueError("unknown rate kind")
    old_bp = int(account[column])
    if not minimum <= int(new_bp) <= maximum:
        raise ValueError("rate is outside allowed range")
    if abs(int(new_bp) - old_bp) > max_delta:
        raise ValueError("daily rate change is too large")
    if account[last_column] == current.date().isoformat():
        raise ValueError("rate has already been changed today")
    conn.execute(
        f"UPDATE bank_accounts SET {column}=?, {last_column}=? WHERE chat_id=?",
        (int(new_bp), current.date().isoformat(), chat_id),
    )
    conn.execute(
        """
        INSERT INTO bank_rate_changes (
            chat_id, changed_at, changed_date, user_id, rate_kind, old_bp, new_bp
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chat_id,
            current.isoformat(timespec="seconds"),
            current.date().isoformat(),
            user_id,
            rate_kind,
            old_bp,
            int(new_bp),
        ),
    )
