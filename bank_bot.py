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


def _menu_keyboard(managed: bool = False):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🏦 Состояние банка", callback_data="bank:status"),
        InlineKeyboardButton(text="👤 Мои договоры", callback_data="bank:mine"),
    )
    kb.row(
        InlineKeyboardButton(text="➕ Открыть вклад", callback_data="bank:deposit"),
        InlineKeyboardButton(text="💳 Получить кредит", callback_data="bank:credit"),
    )
    kb.row(
        InlineKeyboardButton(text="⚙️ Действия", callback_data="bank:actions")
    )
    if managed:
        kb.row(
            InlineKeyboardButton(text="🛠 Управление", callback_data="bank:manage")
        )
    return kb.as_markup()


def _back_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="← В банк", callback_data="bank:menu")
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


def _mine_text(conn, chat_id: int, user_id: int) -> str:
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
    quote = bank_core.credit_quote(conn, chat_id, user_id)
    lines = [
        "👤 МОИ ДОГОВОРЫ",
        "",
        f"Кредитный рейтинг: {quote['rating']}",
        f"Средний доход: {_sits(quote['average_income_milli'])} сит/день",
        f"Исходный лимит: {_sits(quote['raw_limit_milli'])} сит",
        f"Доступно сейчас: {_sits(quote['available_limit_milli'])} сит",
    ]
    if quote["default_debt_milli"]:
        lines.append(f"Дефолтный долг: {_sits(quote['default_debt_milli'])} сит")
    lines.extend(["", "Вклад:"])
    if deposit:
        lines.extend(
            [
                f"• тело: {_sits(deposit['principal_milli'])} сит",
                f"• ставка: {_pct(deposit['rate_bp'])} в неделю",
                f"• срок: {deposit['maturity_date']}",
                f"• договорная сумма до налога на доход: {_sits(deposit['maturity_milli'])} сит",
                f"• автопродление: {'включено' if deposit['auto_renew'] else 'выключено'}",
            ]
        )
    else:
        lines.append("• активного вклада нет")
    lines.extend(["", "Кредит:"])
    if loan:
        overdue = conn.execute(
            """
            SELECT COUNT(*) AS n, COALESCE(SUM(amount_milli),0) AS total
            FROM bank_loan_payments WHERE loan_id=? AND status='overdue'
            """,
            (int(loan["id"]),),
        ).fetchone()
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
                f"• получено: {_sits(loan['principal_milli'])} сит",
                f"• ставка: {_pct(loan['rate_bp'])} в неделю",
                f"• выплачено: {_sits(loan['paid_milli'])} из {_sits(loan['total_milli'])} сит",
                f"• следующий платёж: {next_payment['due_date']} — {_sits(next_payment['amount_milli'])} сит"
                if next_payment
                else "• платежи завершены",
                f"• просрочек: {overdue['n']} на {_sits(overdue['total'])} сит",
            ]
        )
    else:
        lines.append("• активного кредита нет")
    return "\n".join(lines)


async def _show_menu(message: types.Message, user_id: int) -> None:
    with closing(db.get_connection()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        bank_core.ensure_account(conn, int(message.chat.id), _now())
        managed = _is_bank_manager(conn, int(message.chat.id), user_id)
        text = _bank_status_text(conn, int(message.chat.id))
        conn.commit()
    await message.answer(text, reply_markup=_menu_keyboard(managed))


def register_handlers(dp: Dispatcher) -> None:
    @dp.message(Command("bank"))
    async def bank_command(message: types.Message, state: FSMContext):
        if not message.from_user or message.chat.id >= 0:
            await message.answer("Банк доступен только в групповом чате.")
            return
        await state.clear()
        await _show_menu(message, message.from_user.id)

    @dp.callback_query(F.data == "bank:menu")
    async def bank_menu(query: types.CallbackQuery, state: FSMContext):
        await state.clear()
        if not query.message or query.message.chat.id >= 0:
            await query.answer("Банк доступен только в групповом чате.", show_alert=True)
            return
        with closing(db.get_connection()) as conn:
            bank_core.ensure_account(conn, query.message.chat.id, _now())
            managed = _is_bank_manager(conn, query.message.chat.id, query.from_user.id)
            text = _bank_status_text(conn, query.message.chat.id)
            conn.commit()
        await query.message.edit_text(text, reply_markup=_menu_keyboard(managed))
        await query.answer()

    @dp.callback_query(F.data == "bank:status")
    async def bank_status(query: types.CallbackQuery):
        with closing(db.get_connection()) as conn:
            text = _bank_status_text(conn, query.message.chat.id)
            conn.commit()
        await query.message.edit_text(text, reply_markup=_back_keyboard())
        await query.answer()

    @dp.callback_query(F.data == "bank:mine")
    async def bank_mine(query: types.CallbackQuery):
        with closing(db.get_connection()) as conn:
            text = _mine_text(conn, query.message.chat.id, query.from_user.id)
            conn.commit()
        await query.message.edit_text(text, reply_markup=_back_keyboard())
        await query.answer()

    @dp.callback_query(F.data == "bank:deposit")
    async def bank_deposit_start(query: types.CallbackQuery, state: FSMContext):
        kb = InlineKeyboardBuilder()
        for term in bank_core.ALLOWED_TERMS:
            kb.button(text=f"{term} нед.", callback_data=f"bank:deposit:term:{term}")
        kb.adjust(3)
        kb.row(InlineKeyboardButton(text="← В банк", callback_data="bank:menu"))
        await state.clear()
        await query.message.edit_text("Выберите срок вклада:", reply_markup=kb.as_markup())
        await query.answer()

    @dp.callback_query(F.data.startswith("bank:deposit:term:"))
    async def bank_deposit_term(query: types.CallbackQuery, state: FSMContext):
        term = int(query.data.rsplit(":", 1)[1])
        await state.set_state(BankStates.deposit_amount)
        await state.update_data(chat_id=query.message.chat.id, term_weeks=term)
        await query.message.edit_text(
            "Введите сумму вклада от 10 до 1000 сит одним сообщением."
        )
        await query.answer()

    @dp.message(BankStates.deposit_amount)
    async def bank_deposit_amount(message: types.Message, state: FSMContext):
        data = await state.get_data()
        if message.chat.id != data.get("chat_id"):
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
        kb.button(text="Да", callback_data="bank:deposit:auto:1")
        kb.button(text="Нет", callback_data="bank:deposit:auto:0")
        await message.answer(
            "Автоматически продлить тело вклада на тот же срок по новой ставке? "
            "Доход будет выплачен отдельно в конце срока.",
            reply_markup=kb.as_markup(),
        )

    @dp.callback_query(BankStates.deposit_auto, F.data.startswith("bank:deposit:auto:"))
    async def bank_deposit_auto(query: types.CallbackQuery, state: FSMContext):
        enabled = query.data.endswith(":1")
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
        kb.button(text="✅ Открыть", callback_data="bank:deposit:confirm")
        kb.button(text="Отмена", callback_data="bank:menu")
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

    @dp.callback_query(BankStates.deposit_confirm, F.data == "bank:deposit:confirm")
    async def bank_deposit_confirm(query: types.CallbackQuery, state: FSMContext):
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
                f"✅ Вклад №{deposit_id} открыт.", reply_markup=_back_keyboard()
            )
        except (ValueError, db.InsufficientSitsError) as exc:
            await query.answer(str(exc), show_alert=True)

    @dp.callback_query(F.data == "bank:credit")
    async def bank_credit_start(query: types.CallbackQuery, state: FSMContext):
        with closing(db.get_connection()) as conn:
            quote = bank_core.credit_quote(conn, query.message.chat.id, query.from_user.id)
            conn.commit()
        if not quote["history_eligible"]:
            await query.answer("Нужно 7 календарных дней истории сообщений.", show_alert=True)
            return
        if quote["available_limit_milli"] < bank_core.MIN_CREDIT_MILLI:
            await query.answer("Сейчас доступный лимит меньше 10 сит.", show_alert=True)
            return
        kb = InlineKeyboardBuilder()
        for term in bank_core.ALLOWED_TERMS:
            kb.button(text=f"{term} нед.", callback_data=f"bank:credit:term:{term}")
        kb.adjust(3)
        kb.row(InlineKeyboardButton(text="← В банк", callback_data="bank:menu"))
        await state.clear()
        await query.message.edit_text(
            f"Доступный лимит: {_sits(quote['available_limit_milli'])} сит\n"
            f"Ваша ставка: {_pct(quote['credit_rate_bp'])} в неделю\n\n"
            "Выберите срок:",
            reply_markup=kb.as_markup(),
        )
        await query.answer()

    @dp.callback_query(F.data.startswith("bank:credit:term:"))
    async def bank_credit_term(query: types.CallbackQuery, state: FSMContext):
        term = int(query.data.rsplit(":", 1)[1])
        await state.set_state(BankStates.credit_amount)
        await state.update_data(chat_id=query.message.chat.id, term_weeks=term)
        await query.message.edit_text("Введите желаемую сумму кредита одним сообщением.")
        await query.answer()

    @dp.message(BankStates.credit_amount)
    async def bank_credit_amount(message: types.Message, state: FSMContext):
        data = await state.get_data()
        if message.chat.id != data.get("chat_id"):
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
        kb.button(text="✅ Получить", callback_data="bank:credit:confirm")
        kb.button(text="Отмена", callback_data="bank:menu")
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

    @dp.callback_query(BankStates.credit_confirm, F.data == "bank:credit:confirm")
    async def bank_credit_confirm(query: types.CallbackQuery, state: FSMContext):
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
                f"✅ Кредит №{loan_id} выдан.", reply_markup=_back_keyboard()
            )
        except (ValueError, db.InsufficientSitsError) as exc:
            await query.answer(str(exc), show_alert=True)

    @dp.callback_query(F.data == "bank:actions")
    async def bank_actions(query: types.CallbackQuery):
        kb = InlineKeyboardBuilder()
        kb.button(text="Автопродление вкл/выкл", callback_data="bank:toggle_renew")
        kb.button(text="Закрыть вклад досрочно", callback_data="bank:close_deposit")
        kb.button(text="Погасить просрочки", callback_data="bank:pay_overdue")
        kb.button(text="Погасить кредит досрочно", callback_data="bank:close_credit")
        kb.adjust(1)
        kb.row(InlineKeyboardButton(text="← В банк", callback_data="bank:menu"))
        await query.message.edit_text("Выберите действие:", reply_markup=kb.as_markup())
        await query.answer()

    @dp.callback_query(F.data == "bank:toggle_renew")
    async def bank_toggle_renew(query: types.CallbackQuery):
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

    @dp.callback_query(F.data.in_({"bank:close_deposit", "bank:pay_overdue", "bank:close_credit"}))
    async def bank_confirm_action(query: types.CallbackQuery):
        labels = {
            "bank:close_deposit": ("Досрочно закрыть вклад?", "bank:do_close_deposit"),
            "bank:pay_overdue": ("Погасить все открытые просрочки?", "bank:do_pay_overdue"),
            "bank:close_credit": ("Полностью погасить кредит досрочно?", "bank:do_close_credit"),
        }
        text, callback = labels[query.data]
        kb = InlineKeyboardBuilder()
        kb.button(text="Подтвердить", callback_data=callback)
        kb.button(text="Отмена", callback_data="bank:actions")
        await query.message.edit_text(text, reply_markup=kb.as_markup())
        await query.answer()

    @dp.callback_query(F.data.in_({"bank:do_close_deposit", "bank:do_pay_overdue", "bank:do_close_credit"}))
    async def bank_do_action(query: types.CallbackQuery):
        try:
            with closing(db.get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                if query.data == "bank:do_close_deposit":
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
                elif query.data == "bank:do_pay_overdue":
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
            await query.message.edit_text(f"✅ {result}", reply_markup=_back_keyboard())
        except (ValueError, db.InsufficientSitsError) as exc:
            await query.answer(str(exc), show_alert=True)

    @dp.callback_query(F.data == "bank:manage")
    async def bank_manage(query: types.CallbackQuery):
        with closing(db.get_connection()) as conn:
            allowed = _is_bank_manager(conn, query.message.chat.id, query.from_user.id)
        if not allowed:
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        kb = InlineKeyboardBuilder()
        kb.button(text="Изменить ключевую ставку", callback_data="bank:rate:key")
        kb.button(text="Изменить налог", callback_data="bank:rate:tax")
        kb.button(text="Полный отчёт", callback_data="bank:manager_report")
        kb.adjust(1)
        kb.row(InlineKeyboardButton(text="← В банк", callback_data="bank:menu"))
        await query.message.edit_text("Управление банком:", reply_markup=kb.as_markup())
        await query.answer()

    @dp.callback_query(F.data.startswith("bank:rate:"))
    async def bank_rate_start(query: types.CallbackQuery, state: FSMContext):
        kind = query.data.rsplit(":", 1)[1]
        with closing(db.get_connection()) as conn:
            allowed = _is_bank_manager(conn, query.message.chat.id, query.from_user.id)
        if not allowed:
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        await state.set_state(BankStates.rate_value)
        await state.update_data(chat_id=query.message.chat.id, rate_kind=kind)
        await query.message.edit_text(
            "Введите новую ставку в процентах."
            + (" Шаг за сутки — не более 5 п.п." if kind == "key" else " Шаг за сутки — не более 1 п.п.")
        )
        await query.answer()

    @dp.message(BankStates.rate_value)
    async def bank_rate_value(message: types.Message, state: FSMContext):
        data = await state.get_data()
        if message.chat.id != data.get("chat_id"):
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
            await message.answer(f"✅ Новая ставка: {_pct(new_bp)}", reply_markup=_back_keyboard())
        except ValueError as exc:
            await message.answer(str(exc))

    @dp.callback_query(F.data == "bank:manager_report")
    async def bank_manager_report(query: types.CallbackQuery):
        with closing(db.get_connection()) as conn:
            if not _is_bank_manager(conn, query.message.chat.id, query.from_user.id):
                await query.answer("Недостаточно прав.", show_alert=True)
                return
            text = _bank_status_text(conn, query.message.chat.id)
        await query.message.edit_text(text, reply_markup=_back_keyboard())
        await query.answer()


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
