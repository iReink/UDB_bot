# sticker_manager.py
import asyncio
import logging
from datetime import datetime, timedelta, timezone
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None

# ====== НАСТРОЙКИ ======
# Стикер, который отправляем при тишине
SILENCE_STICKER_ID = "CAACAgIAAyEFAASixe81AAEBKBZonrxM7qEb65AQWLINQj-igCqgZQACjHYAAu1RQErYR3VajrrA1TYE"

# Временное окно (по часовому поясу TZ_NAME): с 11:00 до 21:00
WINDOW_START_HOUR = 11
WINDOW_END_HOUR = 21

# Порог тишины
SILENCE_DELTA = timedelta(hours=2)

# Часовой пояс для окна времени (если не нужен — можно оставить None, тогда используется локальное время сервера)
TZ_NAME = None  # при желании поменяй на свой

# Периодичность проверки условий
CHECK_INTERVAL_SECONDS = 300  # 5 минут

# ====== ВНУТРЕННЕЕ СОСТОЯНИЕ ======
# чаты, в которых бот «замечен» и за которыми следим
_known_chats: set[int] = set()

# время последнего сообщения в чате (UTC-aware)
_last_message_time_utc: dict[int, datetime] = {}

# в какие даты уже отправляли «тихий» стикер (дата в TZ_NAME, чтобы «раз в день» считалось по нужной зоне)
_last_sent_date_local: dict[int, datetime.date] = {}

# подготовка TZ
_TZ = ZoneInfo(TZ_NAME) if ZoneInfo and TZ_NAME else None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_local() -> datetime:
    if _TZ:
        return _now_utc().astimezone(_TZ)
    # fallback: локальное время сервера
    return datetime.now()


def seed_known_chats_from_stats(stats: dict) -> None:
    """
    Инициализация списка чатов на основе ключей из stats.json.
    Полезно для того, чтобы корутина начала мониторинг сразу,
    даже если после рестарта ещё не приходили новые сообщения.
    """
    for chat_id in stats.keys():
        try:
            _known_chats.add(int(chat_id))
        except Exception:
            # если ключи строковые — пробуем привести; если нет — пропускаем
            pass


def note_activity(chat_id: int, message_dt) -> None:
    """
    Отметить активность в чате.
    Вызывай ЭТО при любом входящем сообщении (включая команды).
    :param message_dt: datetime сообщения (может быть naive/aware; сконвертим в UTC)
    """
    global _known_chats

    _known_chats.add(int(chat_id))

    # приведение времени к UTC-aware
    if isinstance(message_dt, datetime):
        if message_dt.tzinfo is None:
            # считаем, что это UTC-naive от Telegram (чаще всего это так в aiogram)
            dt_utc = message_dt.replace(tzinfo=timezone.utc)
        else:
            dt_utc = message_dt.astimezone(timezone.utc)
    else:
        # если по какой-то причине не передали datetime — используем текущее UTC
        dt_utc = _now_utc()

    prev = _last_message_time_utc.get(chat_id)
    if not prev or dt_utc > prev:
        _last_message_time_utc[chat_id] = dt_utc


async def silence_checker_task(bot, check_interval_seconds: int = CHECK_INTERVAL_SECONDS) -> None:
    """
    Фоновая задача: каждые check_interval_seconds проверяет для КАЖДОГО известного чата:
    1) тишина >= 2 часов
    2) локальное время в окне [11:00, 21:00)
    3) в текущую локальную дату ещё НЕ отправляли этот стикер
    При выполнении условий — отправляет стикер и помечает дату как «отправлено».
    """
    while True:
        try:
            now_utc = _now_utc()
            now_local = _now_local()

            # проверяем только внутри окна времени
            if WINDOW_START_HOUR <= now_local.hour < WINDOW_END_HOUR:
                for chat_id in list(_known_chats):
                    last_msg_utc = _last_message_time_utc.get(chat_id)
                    if not last_msg_utc:
                        # нет данных — пока не рискуем
                        continue

                    # условие 1: тишина >= 2 часов (считаем в UTC)
                    if now_utc - last_msg_utc < SILENCE_DELTA:
                        continue

                    # условие 3: сегодня ещё не отправляли (в локальной зоне)
                    if _last_sent_date_local.get(chat_id) == now_local.date():
                        continue

                    # все условия выполнены — отправляем
                    try:
                        await bot.send_sticker(chat_id, SILENCE_STICKER_ID)
                        _last_sent_date_local[chat_id] = now_local.date()
                        logging.info(f"[silence_checker] sent sticker to chat {chat_id} at {now_local.isoformat()}")
                    except Exception as e:
                        logging.exception(f"[silence_checker] failed to send sticker to chat {chat_id}: {e}")

        except Exception:
            logging.exception("[silence_checker] loop exception")

        await asyncio.sleep(check_interval_seconds)
