import os
import datetime
import pytz # Добавляем импорт pytz
import logging

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ====== НАСТРОЙКИ GOOGLE КАЛЕНДАРЯ ======
# Путь к файлу ключа сервисного аккаунта
SERVICE_ACCOUNT_FILE = 'udb-calendar-473312-a903afa9b42d.json'
# ID чата, для которого будет работать интеграция (для начала)
TARGET_CHAT_ID = -1002737417162
# ID календаря, в который будут добавляться события
GOOGLE_CALENDAR_ID = '50f3fcd4b16b3168141db528c2b51843620dd5360b4d808f649dbd691c3b93cb@group.calendar.google.com'

# Области доступа (scopes) для Google Calendar API
SCOPES = ['https://www.googleapis.com/auth/calendar.events']

logger = logging.getLogger(__name__)

def get_calendar_service():
    """Аутентификация с помощью сервисного аккаунта и получение объекта сервиса Google Calendar."""
    try:
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        service = build('calendar', 'v3', credentials=credentials)
        return service
    except Exception as e:
        logger.error(f"Ошибка аутентификации или инициализации сервиса Google Calendar: {e}")
        return None

async def create_calendar_event(
    chat_id: int,
    daily_name: str,
    daily_description: str,
    daily_datetime: datetime.datetime,
    daily_link: str | None,
    daily_id: int,
    bot_instance # Передаем экземпляр бота для отправки сообщений
) -> str | None:
    """
    Создает событие в Google Календаре для данного дейлика.
    """
    if chat_id != TARGET_CHAT_ID:
        logger.info(f"Дейлик создан не в целевом чате {TARGET_CHAT_ID}, пропускаем интеграцию с Google Календарем.")
        return False

    service = get_calendar_service()
    if not service:
        logger.error("Не удалось получить сервис Google Календаря. Пропускаем создание события.")
        return False

    # Время начала и окончания события
    # Предполагаем, что событие длится 1 час, если не указано иное
    start_time = daily_datetime
    end_time = daily_datetime + datetime.timedelta(hours=1)

    description_parts = [
        f"Описание: {daily_description}",
    ]
    if daily_link:
        description_parts.append(f"Ссылка: {daily_link}")
    description_parts.append(f"Создано через Telegram бота. ID дейлика: {daily_id}")

    event = {
        'summary': daily_name,
        'description': "\n".join(description_parts),
        'start': {
            'dateTime': start_time.isoformat(),
            'timeZone': 'Asia/Yekaterinburg', # Меняем на GMT+5
        },
        'end': {
            'dateTime': end_time.isoformat(),
            'timeZone': 'Asia/Yekaterinburg', # Меняем на GMT+5
        },
    }

    try:
        event = service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()
        logger.info(f"Событие создано в Google Календаре: {event.get('htmlLink')}")
        await bot_instance.send_message(
            chat_id,
            f"✅ Дейлик добавлен в Google Календарь: <a href=\"{event.get('htmlLink')}\">Посмотреть в Календаре</a>",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        return event.get('id') # Возвращаем ID созданного события
    except HttpError as error:
        logger.error(f"Ошибка при создании события в Google Календаре: {error}")
        await bot_instance.send_message(
            chat_id,
            f"❌ Не удалось добавить дейлик в Google Календарь. Ошибка: {error}",
            parse_mode="HTML"
        )
        return None
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при работе с Google Календарем: {e}")
        await bot_instance.send_message(
            chat_id,
            f"❌ Произошла непредвиденная ошибка при добавлении в Google Календарь: {e}",
            parse_mode="HTML"
        )
        return None

async def update_calendar_event(
    calendar_event_id: str,
    chat_id: int,
    daily_name: str,
    daily_description: str,
    daily_datetime: datetime.datetime,
    daily_link: str | None,
    daily_id: int,
    bot_instance # Передаем экземпляр бота для отправки сообщений
) -> bool:
    """
    Обновляет существующее событие в Google Календаре для данного дейлика.
    """
    if chat_id != TARGET_CHAT_ID:
        logger.info(f"Дейлик создан не в целевом чате {TARGET_CHAT_ID}, пропускаем интеграцию с Google Календарем.")
        return False

    service = get_calendar_service()
    if not service:
        logger.error("Не удалось получить сервис Google Календаря. Пропускаем обновление события.")
        return False

    start_time = daily_datetime
    end_time = daily_datetime + datetime.timedelta(hours=1)

    description_parts = [
        f"Описание: {daily_description}",
    ]
    if daily_link:
        description_parts.append(f"Ссылка: {daily_link}")
    description_parts.append(f"Обновлено через Telegram бота. ID дейлика: {daily_id}")

    event = {
        'summary': daily_name,
        'description': "\n".join(description_parts),
        'start': {
            'dateTime': start_time.isoformat(),
            'timeZone': 'Asia/Yekaterinburg',
        },
        'end': {
            'dateTime': end_time.isoformat(),
            'timeZone': 'Asia/Yekaterinburg',
        },
    }

    try:
        updated_event = service.events().update(calendarId=GOOGLE_CALENDAR_ID, eventId=calendar_event_id, body=event).execute()
        logger.info(f"Событие обновлено в Google Календаре: {updated_event.get('htmlLink')}")
        await bot_instance.send_message(
            chat_id,
            f"✅ Дейлик обновлён в Google Календаре: <a href=\"{updated_event.get('htmlLink')}\">Посмотреть в Календаре</a>",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        return True
    except HttpError as error:
        logger.error(f"Ошибка при обновлении события в Google Календаре: {error}")
        await bot_instance.send_message(
            chat_id,
            f"❌ Не удалось обновить дейлик в Google Календаре. Ошибка: {error}",
            parse_mode="HTML"
        )
        return False
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при работе с Google Календарем: {e}")
        await bot_instance.send_message(
            chat_id,
            f"❌ Произошла непредвиденная ошибка при обновлении в Google Календаре: {e}",
            parse_mode="HTML"
        )
        return False

# Инициализируем логгер
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
