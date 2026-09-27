"""Telegram UI and scheduler for the per-chat sit bank."""

from __future__ import annotations

import asyncio
import logging
import math
from contextlib import closing
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from aiogram import Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import bank_core
import db
from settings import ADMIN_IDS
from sits import format_sits, parse_sits


BANK_TZ = ZoneInfo("Asia/Yekaterinburg")


class BankStates(StatesGroup):
    deposit_amount = State()
    deposit_auto = State()
    deposit_confirm = State()
    credit_amount = State()
    credit_confirm = State()
    rate_value = State()


def _now() -> datetime:
    return datetime.now(BANK_TZ).replace(tzinfo=None)


def _pct(bp: int) -> str:
    value = Decimal(int(bp)) / 100
    return f"{value.quantize(Decimal('0.01')).normalize()}%"


def _sits(milli: int) -> str:
    return format_sits(bank_core.milli_to_sits(int(milli)))


def _coverage(value: float) -> str:
    return "∞" if math.isinf(value) else f"{value * 100:.1f}%"


STATE_LABELS = {
    "normal": "🟢 Норма",
    "tension": "🟡 Напряжение",
    "deficit": "🟠 Дефицит",
    "reserve": "🔴 Резерв",
    "crisis": "🚨 Кассовый кризис",
}


def _is_bank_manager(conn, chat_id: int, user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    row = conn.execute(
        "SELECT user_id FROM bank_ministers WHERE chat_id=?", (chat_id,)
    ).fetchone()
    return bool(row and int(row["user_id"]) == user_id)


def _callback(owner_id: int, action: str, value: int | str | None = None) -> str:
    parts = ["bank", str(int(owner_id)), action]
    if value is not None:
        parts.append(str(value))
    return ":".join(parts)


def _callback_parts(query: types.CallbackQuery) -> list[str]:
    return (query.data or "").split(":")


async def _require_menu_owner(query: types.CallbackQuery) -> list[str] | None:
    parts = _callback_parts(query)
    try:
        owner_id = int(parts[1])
    except (IndexError, TypeError, ValueError):
        await query.answer("Меню устарело. Вызовите /bank ещё раз.", show_alert=True)
        return None
    if owner_id != query.from_user.id:
        await query.answer(
            "Это меню другого пользователя. Вызовите /bank, чтобы открыть своё.",
            show_alert=True,
        )
        return None
    return parts


def _user_bank_state(conn, chat_id: int, user_id: int) -> dict:
    deposit = conn.execute(
        """
        SELECT * FROM bank_deposits
        WHERE chat_id=? AND user_id=? AND status='active'
        """,
        (chat_id, user_id),
    ).fetchone()
    loan = conn.execute(
        """
        SELECT * FROM bank_loans
        WHERE chat_id=? AND user_id=? AND status='active'
        """,
        (chat_id, user_id),
    ).fetchone()
    overdue = None
    if loan:
        overdue = conn.execute(
            """
            SELECT COUNT(*) AS n, COALESCE(SUM(amount_milli),0) AS total
            FROM bank_loan_payments WHERE loan_id=? AND status='overdue'
            """,
            (int(loan["id"]),),
        ).fetchone()
    profile = conn.execute(
        """
        SELECT rating, default_debt_milli FROM bank_credit_profiles
        WHERE chat_id=? AND user_id=?
        """,
        (chat_id, user_id),
    ).fetchone()
    claim = conn.execute(
        """
        SELECT COALESCE(SUM(principal_remaining_milli + interest_remaining_milli),0) AS total
        FROM bank_deposit_claims
        WHERE chat_id=? AND user_id=? AND status='open'
        """,
        (chat_id, user_id),
    ).fetchone()
    return {
        "deposit": deposit,
        "loan": loan,
        "overdue_count": int(overdue["n"] or 0) if overdue else 0,
        "overdue_milli": int(overdue["total"] or 0) if overdue else 0,
        "rating": int(profile["rating"]) if profile else 0,
        "default_debt_milli": int(profile["default_debt_milli"] or 0) if profile else 0,
        "claim_milli": int(claim["total"] or 0) if claim else 0,
    }


def _user_balance_milli(conn, chat_id: int, user_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(sits,0) AS sits FROM users WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()
    return bank_core.sits_to_milli(row["sits"] if row else 0)


def _deposit_opening_error(user_state: dict) -> str | None:
    if user_state["deposit"]:
        return "Одновременно можно иметь только один активный вклад"
    return None


def _menu_keyboard(owner_id: int, managed: bool = False, has_contracts: bool = False):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="➕ Открыть вклад", callback_data=_callback(owner_id, "deposit")
        ),
        InlineKeyboardButton(
            text="💳 Получить кредит", callback_data=_callback(owner_id, "credit")
        ),
    )
    if has_contracts:
        kb.row(
            InlineKeyboardButton(
                text="👤 Мои договоры", callback_data=_callback(owner_id, "mine")
            )
        )
    if managed:
        kb.row(
            InlineKeyboardButton(
                text="🛠 Управление", callback_data=_callback(owner_id, "manage")
            )
        )
    return kb.as_markup()


def _back_keyboard(owner_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="← В банк", callback_data=_callback(owner_id, "menu"))
    return kb.as_markup()


def _bank_status_text(conn, chat_id: int) -> str:
    metrics = bank_core.bank_metrics(conn, chat_id, _now().date())
    deposit_rate, credit_rate = bank_core.offered_rates(conn, chat_id)
    return (
        "🏦 СИТ-БАНК\n\n"
        f"Состояние: {STATE_LABELS[metrics['state']]}\n"
        f"Ключевая ставка: {_pct(metrics['key_rate_bp'])}\n"
        f"Налог: {_pct(metrics['tax_rate_bp'])}\n"
        f"Новый вклад: {_pct(deposit_rate)} в неделю\n"
        f"Базовый кредит: {_pct(credit_rate)} в неделю\n\n"
        f"Ликвидность: {_sits(metrics['liquidity_milli'])} сит\n"
        f"Свободная ликвидность: {_sits(metrics['free_liquidity_milli'])} сит\n"
        f"Резерв: {_sits(metrics['reserve_milli'])} сит\n"
        f"Coverage: {_coverage(metrics['coverage'])}\n"
        f"U: {metrics['utilization'] * 100:.1f}%\n\n"
        f"Капитал: {_sits(metrics['capital_milli'])} сит\n"
        f"Зарезервировано под проценты: {_sits(metrics['reserved_capital_milli'])} сит\n"
        f"Свободный капитал: {_sits(metrics['free_capital_milli'])} сит\n\n"
        f"Вклады к выплате завтра: {_sits(metrics['payout_tomorrow_milli'])} сит\n"
        f"Вклады в горизонте 2 дней: {_sits(metrics['payout_two_days_milli'])} сит\n"
        f"Просроченных платежей: {metrics['overdue_payments']}\n"
        f"Дефолтный долг: {_sits(metrics['default_debt_milli'])} сит"
    )


def _personal_menu_text(conn, chat_id: int, user_id: int, state: dict | None = None) -> str:
    state = state or _user_bank_state(conn, chat_id, user_id)
    lines = ["🏦 СИТ-БАНК"]
    if state["default_debt_milli"]:
        lines.extend(
            ["", f"⚠️ Остаток долга после дефолта: {_sits(state['default_debt_milli'])} сит"]
        )
    elif state["overdue_count"]:
        lines.extend(
            [
                "",
                f"⚠️ Просрочено платежей: {state['overdue_count']} на {_sits(state['overdue_milli'])} сит",
            ]
        )
    deposit = state["deposit"]
    if deposit:
        metrics = bank_core.bank_metrics(conn, chat_id, _now().date())
        interest = int(deposit["maturity_milli"]) - int(deposit["principal_milli"])
        net_interest = interest - bank_core.mul_bp(interest, int(metrics["tax_rate_bp"]))
        lines.extend(
            [
                "",
                f"💰 Выплата по вкладу {deposit['maturity_date']}: около "
                f"{_sits(int(deposit['principal_milli']) + net_interest)} сит",
            ]
        )
    if state["claim_milli"]:
        lines.extend(
            ["", f"⏳ Ожидает выплаты по завершённому вкладу: {_sits(state['claim_milli'])} сит"]
        )
    lines.extend(["", "Выберите действие:"])
    return "\n".join(lines)


def _mine_text(conn, chat_id: int, user_id: int, state: dict | None = None) -> str:
    state = state or _user_bank_state(conn, chat_id, user_id)
    deposit = state["deposit"]
    loan = state["loan"]
    lines = ["👤 МОИ ДОГОВОРЫ"]
    if state["default_debt_milli"]:
        lines.extend(["", f"Долг после дефолта: {_sits(state['default_debt_milli'])} сит"])
    if deposit:
        lines.extend(
            [
                "",
                "Вклад:",
                f"• тело: {_sits(deposit['principal_milli'])} сит",
                f"• ставка: {_pct(deposit['rate_bp'])} в неделю",
                f"• выплата: {deposit['maturity_date']}",
                f"• сумма по договору до налога на доход: {_sits(deposit['maturity_milli'])} сит",
                f"• автопродление: {'включено' if deposit['auto_renew'] else 'выключено'}",
            ]
        )
    if loan:
        next_payment = conn.execute(
            """
            SELECT due_date, amount_milli FROM bank_loan_payments
            WHERE loan_id=? AND status IN ('scheduled','overdue')
            ORDER BY installment_no LIMIT 1
            """,
            (int(loan["id"]),),
        ).fetchone()
        lines.extend(
            [
                "",
                "Кредит:",
                f"• получено: {_sits(loan['principal_milli'])} сит",
                f"• ставка: {_pct(loan['rate_bp'])} в неделю",
                f"• выплачено: {_sits(loan['paid_milli'])} из {_sits(loan['total_milli'])} сит",
                f"• следующий платёж: {next_payment['due_date']} — {_sits(next_payment['amount_milli'])} сит"
                if next_payment
                else "• платежи завершены",
            ]
        )
        if state["overdue_count"]:
            lines.append(
                f"• просрочек: {state['overdue_count']} на {_sits(state['overdue_milli'])} сит"
            )
    if state["claim_milli"]:
        lines.extend(
            ["", f"Ожидает выплаты по завершённому вкладу: {_sits(state['claim_milli'])} сит"]
        )
    return "\n".join(lines)


def _mine_keyboard(owner_id: int, state: dict):
    kb = InlineKeyboardBuilder()
    if state["deposit"]:
        kb.button(
            text="Автопродление вкл/выкл",
            callback_data=_callback(owner_id, "toggle_renew"),
        )
        kb.button(
            text="Закрыть вклад досрочно",
            callback_data=_callback(owner_id, "close_deposit"),
        )
    if state["overdue_count"]:
        kb.button(
            text="Погасить просрочки",
            callback_data=_callback(owner_id, "pay_overdue"),
        )
    if state["loan"]:
        kb.button(
            text="Погасить кредит досрочно",
            callback_data=_callback(owner_id, "close_credit"),
        )
    kb.adjust(1)
    kb.row(
        InlineKeyboardButton(text="← В банк", callback_data=_callback(owner_id, "menu"))
    )
    return kb.as_markup()


async def _show_menu(message: types.Message, user_id: int) -> None:
    with closing(db.get_connection()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        bank_core.ensure_account(conn, int(message.chat.id), _now())
        managed = _is_bank_manager(conn, int(message.chat.id), user_id)
        user_state = _user_bank_state(conn, int(message.chat.id), user_id)
        text = _personal_menu_text(conn, int(message.chat.id), user_id, user_state)
        conn.commit()
    has_contracts = bool(
        user_state["deposit"]
        or user_state["loan"]
        or user_state["default_debt_milli"]
        or user_state["claim_milli"]
    )
    await message.answer(
        text,
        reply_markup=_menu_keyboard(user_id, managed=managed, has_contracts=has_contracts),
    )


def register_handlers(dp: Dispatcher) -> None:
    @dp.message(Command("bank"))
    async def bank_command(message: types.Message, state: FSMContext):
        if not message.from_user or message.chat.id >= 0:
            await message.answer("Банк доступен только в групповом чате.")
            return
        await state.clear()
        await _show_menu(message, message.from_user.id)

    @dp.callback_query(F.data.regexp(r"^bank:\d+:menu$"))
    async def bank_menu(query: types.CallbackQuery, state: FSMContext):
        if not await _require_menu_owner(query):
            return
        await state.clear()
        if not query.message or query.message.chat.id >= 0:
            await query.answer("Банк доступен только в групповом чате.", show_alert=True)
            return
        with closing(db.get_connection()) as conn:
            bank_core.ensure_account(conn, query.message.chat.id, _now())
            managed = _is_bank_manager(conn, query.message.chat.id, query.from_user.id)
            user_state = _user_bank_state(
                conn, query.message.chat.id, query.from_user.id
            )
            text = _personal_menu_text(
                conn, query.message.chat.id, query.from_user.id, user_state
            )
            conn.commit()
        has_contracts = bool(
            user_state["deposit"]
            or user_state["loan"]
            or user_state["default_debt_milli"]
            or user_state["claim_milli"]
        )
        await query.message.edit_text(
            text,
            reply_markup=_menu_keyboard(
                query.from_user.id,
                managed=managed,
                has_contracts=has_contracts,
            ),
        )
        await query.answer()

    @dp.callback_query(F.data.regexp(r"^bank:\d+:mine$"))
    async def bank_mine(query: types.CallbackQuery):
        if not await _require_menu_owner(query):
            return
        with closing(db.get_connection()) as conn:
            user_state = _user_bank_state(
                conn, query.message.chat.id, query.from_user.id
            )
            text = _mine_text(
                conn, query.message.chat.id, query.from_user.id, user_state
            )
            conn.commit()
        await query.message.edit_text(
            text, reply_markup=_mine_keyboard(query.from_user.id, user_state)
        )
        await query.answer()

    @dp.callback_query(F.data.regexp(r"^bank:\d+:deposit$"))
    async def bank_deposit_start(query: types.CallbackQuery, state: FSMContext):
        if not await _require_menu_owner(query):
            return
        with closing(db.get_connection()) as conn:
            user_state = _user_bank_state(
                conn, query.message.chat.id, query.from_user.id
            )
            opening_error = _deposit_opening_error(user_state)
            if not opening_error:
                rate, _ = bank_core.offered_rates(conn, query.message.chat.id)
                tax_rate = int(
                    bank_core.bank_metrics(conn, query.message.chat.id)["tax_rate_bp"]
                )
                balance_milli = _user_balance_milli(
                    conn, query.message.chat.id, query.from_user.id
                )
        if opening_error:
            await query.answer(opening_error, show_alert=True)
            return
        kb = InlineKeyboardBuilder()
        for term in bank_core.ALLOWED_TERMS:
            kb.button(
                text=f"{term} нед.",
                callback_data=_callback(query.from_user.id, "deposit_term", term),
            )
        kb.adjust(3)
        kb.row(
            InlineKeyboardButton(
                text="← В банк", callback_data=_callback(query.from_user.id, "menu")
            )
        )
        await state.clear()
        await query.message.edit_text(
            "Открытие вклада\n\n"
            f"Текущая ставка: {_pct(rate)} в неделю\n"
            f"Налог на доход: {_pct(tax_rate)}\n"
            f"Ваш баланс: {_sits(balance_milli)} сит\n"
            f"Допустимая сумма: {_sits(bank_core.MIN_DEPOSIT_MILLI)}–"
            f"{_sits(bank_core.MAX_DEPOSIT_MILLI)} сит\n\n"
            "Выберите срок вклада:",
            reply_markup=kb.as_markup(),
        )
        await query.answer()

    @dp.callback_query(F.data.regexp(r"^bank:\d+:deposit_term:\d+$"))
    async def bank_deposit_term(query: types.CallbackQuery, state: FSMContext):
        parts = await _require_menu_owner(query)
        if not parts:
            return
        term = int(parts[3])
        if term not in bank_core.ALLOWED_TERMS:
            await query.answer("Недоступный срок вклада.", show_alert=True)
            return
        with closing(db.get_connection()) as conn:
            rate, _ = bank_core.offered_rates(conn, query.message.chat.id)
            balance_milli = _user_balance_milli(
                conn, query.message.chat.id, query.from_user.id
            )
        await state.set_state(BankStates.deposit_amount)
        await state.update_data(
            chat_id=query.message.chat.id,
            user_id=query.from_user.id,
            term_weeks=term,
        )
        await query.message.edit_text(
            f"Вклад на {term} нед.\n"
            f"Текущая ставка: {_pct(rate)} в неделю\n"
            f"Ваш баланс: {_sits(balance_milli)} сит\n\n"
            "Введите сумму вклада от 10 до 1000 сит одним сообщением."
        )
        await query.answer()

    @dp.message(BankStates.deposit_amount)
    async def bank_deposit_amount(message: types.Message, state: FSMContext):
        data = await state.get_data()
        if (
            message.chat.id != data.get("chat_id")
            or not message.from_user
            or message.from_user.id != data.get("user_id")
        ):
            return
        try:
            amount = parse_sits(message.text or "")
            amount_milli = bank_core.sits_to_milli(amount)
            if not bank_core.MIN_DEPOSIT_MILLI <= amount_milli <= bank_core.MAX_DEPOSIT_MILLI:
                raise ValueError
        except (ValueError, TypeError):
            await message.answer("Введите сумму от 10 до 1000 сит.")
            return
        await state.update_data(amount_milli=amount_milli)
        await state.set_state(BankStates.deposit_auto)
        kb = InlineKeyboardBuilder()
        kb.button(
            text="Да", callback_data=_callback(message.from_user.id, "deposit_auto", 1)
        )
        kb.button(
            text="Нет", callback_data=_callback(message.from_user.id, "deposit_auto", 0)
        )
        await message.answer(
            "Автоматически продлить тело вклада на тот же срок по новой ставке? "
            "Доход будет выплачен отдельно в конце срока.",
            reply_markup=kb.as_markup(),
        )

    @dp.callback_query(F.data.regexp(r"^bank:\d+:deposit_auto:[01]$"))
    async def bank_deposit_auto(query: types.CallbackQuery, state: FSMContext):
        parts = await _require_menu_owner(query)
        if not parts:
            return
        if await state.get_state() != BankStates.deposit_auto.state:
            await query.answer("Меню устарело. Начните открытие вклада заново.", show_alert=True)
            return
        enabled = parts[3] == "1"
        data = await state.get_data()
        with closing(db.get_connection()) as conn:
            rate, _ = bank_core.offered_rates(conn, query.message.chat.id)
            tax_rate = int(bank_core.bank_metrics(conn, query.message.chat.id)["tax_rate_bp"])
        interest = bank_core.weekly_interest_milli(
            int(data["amount_milli"]), rate, int(data["term_weeks"])
        )
        net_interest = interest - bank_core.mul_bp(interest, tax_rate)
        await state.update_data(auto_renew=enabled, quoted_rate_bp=rate)
        await state.set_state(BankStates.deposit_confirm)
        kb = InlineKeyboardBuilder()
        kb.button(
            text="✅ Открыть",
            callback_data=_callback(query.from_user.id, "deposit_confirm"),
        )
        kb.button(
            text="Отмена", callback_data=_callback(query.from_user.id, "menu")
        )
        await query.message.edit_text(
            "Подтвердите вклад:\n"
            f"Сумма: {_sits(data['amount_milli'])} сит\n"
            f"Срок: {data['term_weeks']} нед.\n"
            f"Ставка: {_pct(rate)} в неделю\n"
            f"Доход после текущего налога: {_sits(net_interest)} сит\n"
            f"Автопродление: {'да' if enabled else 'нет'}",
            reply_markup=kb.as_markup(),
        )
        await query.answer()

    @dp.callback_query(F.data.regexp(r"^bank:\d+:deposit_confirm$"))
    async def bank_deposit_confirm(query: types.CallbackQuery, state: FSMContext):
        if not await _require_menu_owner(query):
            return
        if await state.get_state() != BankStates.deposit_confirm.state:
            await query.answer("Меню устарело. Начните открытие вклада заново.", show_alert=True)
            return
        data = await state.get_data()
        try:
            with closing(db.get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                deposit_id = bank_core.open_deposit(
                    conn,
                    query.message.chat.id,
                    query.from_user.id,
                    int(data["amount_milli"]),
                    int(data["term_weeks"]),
                    bool(data["auto_renew"]),
                    db.apply_sit_change,
                    now=_now(),
                    expected_rate_bp=int(data["quoted_rate_bp"]),
                )
                conn.commit()
            await state.clear()
            await query.message.edit_text(
                f"✅ Вклад №{deposit_id} открыт.",
                reply_markup=_back_keyboard(query.from_user.id),
            )
        except (ValueError, db.InsufficientSitsError) as exc:
            await query.answer(str(exc), show_alert=True)

    @dp.callback_query(F.data.regexp(r"^bank:\d+:credit$"))
    async def bank_credit_start(query: types.CallbackQuery, state: FSMContext):
        if not await _require_menu_owner(query):
            return
        with closing(db.get_connection()) as conn:
            quote = bank_core.credit_quote(conn, query.message.chat.id, query.from_user.id)
            balance_milli = _user_balance_milli(
                conn, query.message.chat.id, query.from_user.id
            )
            conn.commit()
        if not quote["history_eligible"]:
            await query.answer("Нужно 7 календарных дней истории сообщений.", show_alert=True)
            return
        if quote["available_limit_milli"] < bank_core.MIN_CREDIT_MILLI:
            await query.answer("Сейчас доступный лимит меньше 10 сит.", show_alert=True)
            return
        kb = InlineKeyboardBuilder()
        for term in bank_core.ALLOWED_TERMS:
            kb.button(
                text=f"{term} нед.",
                callback_data=_callback(query.from_user.id, "credit_term", term),
            )
        kb.adjust(3)
        kb.row(
            InlineKeyboardButton(
                text="← В банк", callback_data=_callback(query.from_user.id, "menu")
            )
        )
        await state.clear()
        await query.message.edit_text(
            f"Доступный лимит: {_sits(quote['available_limit_milli'])} сит\n"
            f"Ваша ставка: {_pct(quote['credit_rate_bp'])} в неделю\n\n"
            f"Ваш баланс: {_sits(balance_milli)} сит\n\n"
            "Выберите срок:",
            reply_markup=kb.as_markup(),
        )
        await query.answer()

    @dp.callback_query(F.data.regexp(r"^bank:\d+:credit_term:\d+$"))
    async def bank_credit_term(query: types.CallbackQuery, state: FSMContext):
        parts = await _require_menu_owner(query)
        if not parts:
            return
        term = int(parts[3])
        if term not in bank_core.ALLOWED_TERMS:
            await query.answer("Недоступный срок кредита.", show_alert=True)
            return
        with closing(db.get_connection()) as conn:
            quote = bank_core.credit_quote(
                conn, query.message.chat.id, query.from_user.id
            )
        await state.set_state(BankStates.credit_amount)
        await state.update_data(
            chat_id=query.message.chat.id,
            user_id=query.from_user.id,
            term_weeks=term,
        )
        await query.message.edit_text(
            f"Кредит на {term} нед.\n"
            f"Текущая ставка: {_pct(quote['credit_rate_bp'])} в неделю\n"
            f"Доступный лимит: {_sits(quote['available_limit_milli'])} сит\n\n"
            "Введите желаемую сумму кредита одним сообщением."
        )
        await query.answer()

    @dp.message(BankStates.credit_amount)
    async def bank_credit_amount(message: types.Message, state: FSMContext):
        data = await state.get_data()
        if (
            message.chat.id != data.get("chat_id")
            or not message.from_user
            or message.from_user.id != data.get("user_id")
        ):
            return
        try:
            amount_milli = bank_core.sits_to_milli(parse_sits(message.text or ""))
        except (ValueError, TypeError):
            await message.answer("Введите корректную сумму кредита.")
            return
        with closing(db.get_connection()) as conn:
            quote = bank_core.credit_quote(conn, message.chat.id, message.from_user.id)
            conn.commit()
        if not bank_core.MIN_CREDIT_MILLI <= amount_milli <= int(quote["available_limit_milli"]):
            await message.answer(
                f"Введите сумму от 10 до {_sits(quote['available_limit_milli'])} сит."
            )
            return
        interest = bank_core.weekly_interest_milli(
            amount_milli, int(quote["credit_rate_bp"]), int(data["term_weeks"])
        )
        total = amount_milli + interest
        payment = bank_core._installment_schedule(
            amount_milli, interest, 7 * int(data["term_weeks"])
        )[0][0]
        await state.update_data(
            amount_milli=amount_milli,
            quoted_rate_bp=int(quote["credit_rate_bp"]),
        )
        await state.set_state(BankStates.credit_confirm)
        kb = InlineKeyboardBuilder()
        kb.button(
            text="✅ Получить",
            callback_data=_callback(message.from_user.id, "credit_confirm"),
        )
        kb.button(
            text="Отмена", callback_data=_callback(message.from_user.id, "menu")
        )
        await message.answer(
            "Подтвердите кредит:\n"
            f"Сумма: {_sits(amount_milli)} сит\n"
            f"Срок: {data['term_weeks']} нед.\n"
            f"Ставка: {_pct(quote['credit_rate_bp'])} в неделю\n"
            f"Вернуть всего: {_sits(total)} сит\n"
            f"Ежедневный платёж: около {_sits(payment)} сит\n"
            f"Grace: {data['term_weeks']} дн.",
            reply_markup=kb.as_markup(),
        )

    @dp.callback_query(F.data.regexp(r"^bank:\d+:credit_confirm$"))
    async def bank_credit_confirm(query: types.CallbackQuery, state: FSMContext):
        if not await _require_menu_owner(query):
            return
        if await state.get_state() != BankStates.credit_confirm.state:
            await query.answer("Меню устарело. Начните получение кредита заново.", show_alert=True)
            return
        data = await state.get_data()
        try:
            with closing(db.get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                loan_id = bank_core.open_credit(
                    conn,
                    query.message.chat.id,
                    query.from_user.id,
                    int(data["amount_milli"]),
                    int(data["term_weeks"]),
                    db.apply_sit_change,
                    now=_now(),
                    expected_rate_bp=int(data["quoted_rate_bp"]),
                )
                conn.commit()
            await state.clear()
            await query.message.edit_text(
                f"✅ Кредит №{loan_id} выдан.",
                reply_markup=_back_keyboard(query.from_user.id),
            )
        except (ValueError, db.InsufficientSitsError) as exc:
            await query.answer(str(exc), show_alert=True)

    @dp.callback_query(F.data.regexp(r"^bank:\d+:toggle_renew$"))
    async def bank_toggle_renew(query: types.CallbackQuery):
        if not await _require_menu_owner(query):
            return
        with closing(db.get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT id, auto_renew FROM bank_deposits
                WHERE chat_id=? AND user_id=? AND status='active'
                """,
                (query.message.chat.id, query.from_user.id),
            ).fetchone()
            if not row:
                conn.rollback()
                await query.answer("Активного вклада нет.", show_alert=True)
                return
            enabled = not bool(row["auto_renew"])
            bank_core.set_deposit_auto_renew(
                conn, query.message.chat.id, query.from_user.id, enabled
            )
            conn.commit()
        await query.answer(
            f"Автопродление {'включено' if enabled else 'выключено'}.", show_alert=True
        )

    @dp.callback_query(
        F.data.regexp(r"^bank:\d+:(close_deposit|pay_overdue|close_credit)$")
    )
    async def bank_confirm_action(query: types.CallbackQuery):
        parts = await _require_menu_owner(query)
        if not parts:
            return
        labels = {
            "close_deposit": ("Досрочно закрыть вклад?", "do_close_deposit"),
            "pay_overdue": ("Погасить все открытые просрочки?", "do_pay_overdue"),
            "close_credit": ("Полностью погасить кредит досрочно?", "do_close_credit"),
        }
        text, action = labels[parts[2]]
        kb = InlineKeyboardBuilder()
        kb.button(
            text="Подтвердить", callback_data=_callback(query.from_user.id, action)
        )
        kb.button(
            text="Отмена", callback_data=_callback(query.from_user.id, "mine")
        )
        await query.message.edit_text(text, reply_markup=kb.as_markup())
        await query.answer()

    @dp.callback_query(
        F.data.regexp(r"^bank:\d+:(do_close_deposit|do_pay_overdue|do_close_credit)$")
    )
    async def bank_do_action(query: types.CallbackQuery):
        parts = await _require_menu_owner(query)
        if not parts:
            return
        try:
            with closing(db.get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                if parts[2] == "do_close_deposit":
                    before = conn.execute(
                        "SELECT COALESCE(sits,0) AS sits FROM users WHERE chat_id=? AND user_id=?",
                        (query.message.chat.id, query.from_user.id),
                    ).fetchone()
                    bank_core.close_deposit_early(
                        conn, query.message.chat.id, query.from_user.id,
                        db.apply_sit_change, now=_now(),
                    )
                    after = conn.execute(
                        "SELECT COALESCE(sits,0) AS sits FROM users WHERE chat_id=? AND user_id=?",
                        (query.message.chat.id, query.from_user.id),
                    ).fetchone()
                    amount = bank_core.sits_to_milli(float(after["sits"]) - float(before["sits"]))
                    result = f"Вклад закрыт. Получено: {_sits(amount)} сит."
                elif parts[2] == "do_pay_overdue":
                    amount = bank_core.repay_overdue(
                        conn, query.message.chat.id, query.from_user.id,
                        db.apply_sit_change, now=_now(),
                    )
                    result = f"Просрочки погашены: {_sits(amount)} сит."
                else:
                    amount = bank_core.repay_credit_early(
                        conn, query.message.chat.id, query.from_user.id,
                        db.apply_sit_change, now=_now(),
                    )
                    result = f"Кредит погашен: {_sits(amount)} сит."
                conn.commit()
            await query.message.edit_text(
                f"✅ {result}", reply_markup=_back_keyboard(query.from_user.id)
            )
        except (ValueError, db.InsufficientSitsError) as exc:
            await query.answer(str(exc), show_alert=True)

    @dp.callback_query(F.data.regexp(r"^bank:\d+:manage$"))
    async def bank_manage(query: types.CallbackQuery):
        if not await _require_menu_owner(query):
            return
        with closing(db.get_connection()) as conn:
            allowed = _is_bank_manager(conn, query.message.chat.id, query.from_user.id)
        if not allowed:
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        kb = InlineKeyboardBuilder()
        kb.button(
            text="Изменить ключевую ставку",
            callback_data=_callback(query.from_user.id, "rate", "key"),
        )
        kb.button(
            text="Изменить налог",
            callback_data=_callback(query.from_user.id, "rate", "tax"),
        )
        kb.button(
            text="Полный отчёт",
            callback_data=_callback(query.from_user.id, "manager_report"),
        )
        kb.adjust(1)
        kb.row(
            InlineKeyboardButton(
                text="← В банк", callback_data=_callback(query.from_user.id, "menu")
            )
        )
        await query.message.edit_text("Управление банком:", reply_markup=kb.as_markup())
        await query.answer()

    @dp.callback_query(F.data.regexp(r"^bank:\d+:rate:(key|tax)$"))
    async def bank_rate_start(query: types.CallbackQuery, state: FSMContext):
        parts = await _require_menu_owner(query)
        if not parts:
            return
        kind = parts[3]
        with closing(db.get_connection()) as conn:
            allowed = _is_bank_manager(conn, query.message.chat.id, query.from_user.id)
        if not allowed:
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        await state.set_state(BankStates.rate_value)
        await state.update_data(
            chat_id=query.message.chat.id,
            user_id=query.from_user.id,
            rate_kind=kind,
        )
        await query.message.edit_text(
            "Введите новую ставку в процентах."
            + (" Шаг за сутки — не более 5 п.п." if kind == "key" else " Шаг за сутки — не более 1 п.п.")
        )
        await query.answer()

    @dp.message(BankStates.rate_value)
    async def bank_rate_value(message: types.Message, state: FSMContext):
        data = await state.get_data()
        if (
            message.chat.id != data.get("chat_id")
            or not message.from_user
            or message.from_user.id != data.get("user_id")
        ):
            return
        try:
            value = Decimal((message.text or "").strip().replace(",", "."))
            new_bp = int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        except (InvalidOperation, ValueError):
            await message.answer("Введите процент числом, например 11 или 5,5.")
            return
        try:
            with closing(db.get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                if not _is_bank_manager(conn, message.chat.id, message.from_user.id):
                    raise ValueError("Недостаточно прав")
                bank_core.change_rate(
                    conn, message.chat.id, message.from_user.id,
                    str(data["rate_kind"]), new_bp, now=_now(),
                )
                conn.commit()
            await state.clear()
            await message.answer(
                f"✅ Новая ставка: {_pct(new_bp)}",
                reply_markup=_back_keyboard(message.from_user.id),
            )
        except ValueError as exc:
            await message.answer(str(exc))

    @dp.callback_query(F.data.regexp(r"^bank:\d+:manager_report$"))
    async def bank_manager_report(query: types.CallbackQuery):
        if not await _require_menu_owner(query):
            return
        with closing(db.get_connection()) as conn:
            if not _is_bank_manager(conn, query.message.chat.id, query.from_user.id):
                await query.answer("Недостаточно прав.", show_alert=True)
                return
            text = _bank_status_text(conn, query.message.chat.id)
        await query.message.edit_text(
            text, reply_markup=_back_keyboard(query.from_user.id)
        )
        await query.answer()

    @dp.callback_query(F.data.startswith("bank:"))
    async def bank_stale_callback(query: types.CallbackQuery):
        await query.answer(
            "Меню банка устарело. Вызовите /bank, чтобы открыть новое.",
            show_alert=True,
        )


def _report_text(report: dict) -> str:
    metrics = report["metrics"]
    coverage = metrics["coverage"]
    coverage_text = "∞" if coverage is None else f"{coverage * 100:.1f}%"
    return (
        f"🏦 БАНК — отчёт за {report['run_date']}\n\n"
        f"Состояние: {STATE_LABELS[metrics['state']]}\n"
        f"Ликвидность: {_sits(metrics['liquidity_milli'])} сит\n"
        f"Свободная ликвидность: {_sits(metrics['free_liquidity_milli'])} сит\n"
        f"Резерв: {_sits(metrics['reserve_milli'])} сит\n"
        f"Coverage: {coverage_text}\n"
        f"U: {metrics['utilization'] * 100:.1f}%\n"
        f"Капитал: {_sits(metrics['capital_milli'])} сит\n"
        f"Свободный капитал: {_sits(metrics['free_capital_milli'])} сит\n\n"
        f"Депозитные обязательства: {_sits(metrics['deposit_obligations_milli'])} сит\n"
        f"Кредитный портфель: {_sits(metrics['credit_portfolio_milli'])} сит\n"
        f"Просроченных платежей: {metrics['overdue_payments']}\n"
        f"Дефолтный долг: {_sits(metrics['default_debt_milli'])} сит\n\n"
        f"Кредитные платежи: +{_sits(report['credit_payments_milli'])} сит\n"
        f"Налог за сутки: +{_sits(report['tax_income_milli'])} сит\n"
        f"Получено кредитных процентов: +{_sits(report['credit_interest_income_milli'])} сит\n"
        f"Начислено процентов по вкладам: -{_sits(report['deposit_interest_expense_milli'])} сит\n"
        f"Новые просрочки: {report['new_overdue']}\n"
        f"Новые дефолты: {report['new_defaults']}\n"
        f"Выплаты вкладчикам: {_sits(report['claim_payments_milli'])} сит\n"
        f"Продлено вкладов: {report['renewed_deposits']}"
    )


async def _send_minister_report(bot, chat_id: int, report: dict) -> None:
    with closing(db.get_connection()) as conn:
        row = conn.execute(
            "SELECT user_id FROM bank_ministers WHERE chat_id=?", (chat_id,)
        ).fetchone()
    if not row:
        return
    try:
        await bot.send_message(int(row["user_id"]), _report_text(report))
    except Exception:
        logging.exception("Failed to send bank report chat_id=%s", chat_id)


def _run_clearing_transaction(chat_id: int, run_date: date) -> dict | None:
    with closing(db.get_connection()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT 1 FROM bank_daily_runs WHERE chat_id=? AND run_date=?",
            (chat_id, run_date.isoformat()),
        ).fetchone()
        if existing:
            conn.rollback()
            return None
        report = bank_core.run_daily_clearing(
            conn, chat_id, run_date, db.apply_sit_change,
            now=datetime.combine(run_date, time(23, 0)),
        )
        conn.commit()
        return report


async def _catch_up(bot) -> None:
    today = _now().date()
    with closing(db.get_connection()) as conn:
        accounts = conn.execute(
            "SELECT chat_id, created_date FROM bank_accounts ORDER BY chat_id"
        ).fetchall()
    for account in accounts:
        chat_id = int(account["chat_id"])
        with closing(db.get_connection()) as conn:
            last = conn.execute(
                "SELECT MAX(run_date) AS value FROM bank_daily_runs WHERE chat_id=?",
                (chat_id,),
            ).fetchone()
        cursor = (
            date.fromisoformat(last["value"]) + timedelta(days=1)
            if last and last["value"]
            else date.fromisoformat(account["created_date"])
        )
        while cursor < today:
            report = await asyncio.to_thread(_run_clearing_transaction, chat_id, cursor)
            if report:
                await _send_minister_report(bot, chat_id, report)
            cursor += timedelta(days=1)


async def bank_scheduler(bot) -> None:
    try:
        await _catch_up(bot)
    except Exception:
        logging.exception("Initial bank clearing catch-up failed")
    while True:
        try:
            current = _now()
            with closing(db.get_connection()) as conn:
                accounts = [
                    int(row["chat_id"])
                    for row in conn.execute("SELECT chat_id FROM bank_accounts").fetchall()
                ]
            if current.hour >= 12:
                for chat_id in accounts:
                    with closing(db.get_connection()) as conn:
                        conn.execute("BEGIN IMMEDIATE")
                        offers = bank_core.prepare_renewal_offers(
                            conn, chat_id, current.date()
                        )
                        conn.commit()
                    for offer in offers:
                        try:
                            await bot.send_message(
                                chat_id,
                                f"🏦 Вклад пользователя {offer['user_id']} заканчивается сегодня.\n"
                                f"Тело: {_sits(offer['principal_milli'])} сит\n"
                                f"Новая ставка: {_pct(offer['rate_bp'])}\n"
                                f"Ожидаемый доход после текущего налога: {_sits(offer['expected_net_interest_milli'])} сит\n"
                                "Тело будет продлено, если у банка останется техническая возможность.",
                            )
                        except Exception:
                            logging.exception(
                                "Failed to send deposit renewal notice deposit_id=%s",
                                offer["deposit_id"],
                            )
                        finally:
                            with closing(db.get_connection()) as conn:
                                bank_core.mark_renewal_notified(
                                    conn, int(offer["deposit_id"]), current
                                )
                                conn.commit()
            if current.hour >= 23:
                for chat_id in accounts:
                    report = await asyncio.to_thread(
                        _run_clearing_transaction, chat_id, current.date()
                    )
                    if report:
                        await _send_minister_report(bot, chat_id, report)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Bank scheduler iteration failed")
        await asyncio.sleep(30)
