"""Two-step shop flow for full and partial tapeworm treatment."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import cepen
import db
from sits import format_sits


CONFIRMATION_TTL_SECONDS = 600
FULL = "full"
PARTIAL = "partial"
_PENDING: dict[str, tuple[int, int, str, float]] = {}


@dataclass(frozen=True)
class CureShopResult:
    status: str
    kind: str
    name: str | None
    old: float | None = None
    new: float | None = None


def _predicted_partial_length(length: float) -> float:
    value = Decimal(str(length)) * cepen.PARTIAL_CURE_FACTOR
    rounded = value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return float(max(Decimal(str(cepen.INITIAL_LENGTH)), rounded))


def prepare_or_execute(
    chat_id: int,
    user_id: int,
    kind: str,
    *,
    confirmed: bool,
) -> CureShopResult:
    if kind not in {FULL, PARTIAL}:
        return CureShopResult("invalid", kind, None)
    if not db.cepen_enabled(chat_id):
        return CureShopResult("disabled", kind, None)

    old = cepen.length(chat_id, user_id)
    current_name = cepen.name(chat_id, user_id)
    if old <= 0:
        return CureShopResult("healthy", kind, current_name)
    if kind == PARTIAL and old <= cepen.INITIAL_LENGTH:
        return CureShopResult("minimum", kind, current_name, old, old)

    predicted = 0 if kind == FULL else _predicted_partial_length(old)
    if not confirmed:
        return CureShopResult("confirm", kind, current_name, old, predicted)

    if kind == FULL:
        status = cepen.cure(chat_id, user_id, price=cepen.CURE_PRICE)
        return CureShopResult(status, kind, current_name, old, 0 if status == "cured" else old)

    status, actual_old, actual_new = cepen.partial_cure(chat_id, user_id)
    return CureShopResult(status, kind, current_name, actual_old, actual_new)


def _cleanup_pending(now: float) -> None:
    expired = [token for token, data in _PENDING.items() if data[3] <= now]
    for token in expired:
        _PENDING.pop(token, None)


def _new_confirmation(chat_id: int, user_id: int, kind: str) -> str:
    now = time.monotonic()
    _cleanup_pending(now)
    token = secrets.token_urlsafe(6)
    _PENDING[token] = (chat_id, user_id, kind, now + CONFIRMATION_TTL_SECONDS)
    return token


def _consume_confirmation(
    token: str | None,
    chat_id: int,
    user_id: int,
    kind: str,
) -> bool:
    now = time.monotonic()
    _cleanup_pending(now)
    data = _PENDING.get(token or "")
    if not data or data[:3] != (chat_id, user_id, kind):
        return False
    _PENDING.pop(token or "", None)
    return True


def confirmation_text(result: CureShopResult) -> str:
    worm = cepen.subject_from_name(result.name, capital=True, html_mode=True)
    if result.kind == FULL:
        return (
            "⚠️ <b>Подтвердить полное лечение?</b>\n\n"
            f"{worm} будет удалён полностью.\n"
            f"Стоимость: {format_sits(cepen.CURE_PRICE)} сит.\n\n"
            "Это действие нельзя отменить."
        )
    return (
        "✂️ <b>Подтвердить частичное лечение?</b>\n\n"
        f"{worm}: {format_sits(result.old)} → {format_sits(result.new)} см.\n"
        f"Стоимость: {format_sits(cepen.PARTIAL_CURE_PRICE)} сит."
    )


def confirmation_keyboard(kind: str, token: str) -> InlineKeyboardMarkup:
    label = "✅ Вылечить полностью" if kind == FULL else "✅ Уменьшить на 20%"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=label,
            callback_data=f"shop:cure:confirm:{kind}:{token}",
        )],
        [InlineKeyboardButton(text="↩️ Назад в магазин", callback_data="shop:menu")],
    ])


async def handle(callback, kind: str, *, confirmed: bool, token: str | None = None) -> None:
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    if confirmed and not _consume_confirmation(token, chat_id, user_id, kind):
        await callback.answer(
            "Подтверждение устарело или принадлежит другому пользователю. Открой /shop снова.",
            show_alert=True,
        )
        return

    result = prepare_or_execute(chat_id, user_id, kind, confirmed=confirmed)
    if result.status == "confirm":
        new_token = _new_confirmation(chat_id, user_id, kind)
        await callback.message.edit_text(
            confirmation_text(result),
            reply_markup=confirmation_keyboard(kind, new_token),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    if result.status == "cured":
        worm = cepen.subject_from_name(result.name, capital=True, html_mode=True)
        await callback.message.edit_text(
            f"{worm} у {cepen.mention(chat_id, user_id)} исцелён!",
            parse_mode="HTML",
        )
        await callback.answer()
        return
    if result.status == "reduced":
        worm = cepen.subject_from_name(result.name, capital=True, html_mode=True)
        await callback.message.edit_text(
            f"✂️ {worm} у {cepen.mention(chat_id, user_id)} укорочен на 20%: "
            f"{format_sits(result.old)} → {format_sits(result.new)} см.",
            parse_mode="HTML",
        )
        await callback.answer()
        return

    messages = {
        "insufficient": (
            f"Недостаточно сит. Нужно "
            f"{format_sits(cepen.CURE_PRICE if kind == FULL else cepen.PARTIAL_CURE_PRICE)}."
        ),
        "disabled": "Цепень отключён в этом чате.",
        "healthy": "У тебя нет цепня.",
        "minimum": "Цепень уже минимальной длины — 5 см.",
        "invalid": "Неизвестный способ лечения.",
    }
    await callback.answer(messages.get(result.status, "Не удалось выполнить лечение."), show_alert=True)
