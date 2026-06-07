# UDB_bot

## Web MVP (FastAPI + Telegram auth)

В репозиторий добавлен отдельный веб-бэкенд и фронтенд для браузерного MVP:

- `web/server.py` — FastAPI API + Telegram Login auth + сессии.
- `web/templates/index.html` — страница с модалкой выбора `chat_id`.
- `web/static/*` — UI, переключение аккаунта в хедере, вывод баланса.

### Что делает MVP

1. Авторизация через Telegram Login Widget.
2. Альтернативная авторизация без домена: код из команды `/auth` в ЛС с ботом.
3. После входа открывается модальное окно со списком всех аккаунтов пользователя по паре `user_id;chat_id`.
4. После выбора показывается:
   `Вы вошли через чат <название чата>, ваш баланс <n> сит`
5. В правом верхнем углу доступен выпадающий список всех чатов/ЛС для быстрого переключения.

Примечание: в текущей схеме `stats.db` нет таблицы с названиями групп, поэтому для групп используется подпись `Чат <chat_id>`, а для лички — `ЛС`.

## Локальный запуск веба

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-web.txt
cp .env.web.example .env.web
```

Заполните в `.env.web`:

- `BOT_TOKEN`
- `BOT_USERNAME` (без `@`, нужен только для Telegram Login Widget)
- `WEB_SESSION_SECRET`

Если на странице входа видно `Bot domain invalid`, это не ошибка кода:

1. В `BotFather` выполнить `/setdomain` и указать ваш домен.
2. Открывать сайт по этому домену, не по IP.
3. Для внешнего доступа использовать HTTPS.

Если домена нет, используйте вход по коду:

1. Напишите боту в личные сообщения команду `/auth`.
2. Введите полученный 4-значный код на странице логина.
3. Код одноразовый и ограничен по времени.

Запуск:

```bash
set -a
source .env.web
set +a
uvicorn web.server:app --reload --host 127.0.0.1 --port 8080
```

Открыть в браузере: `http://127.0.0.1:8080`

## Ubuntu VPS (systemd + nginx)

В `deploy/` добавлены шаблоны:

- `deploy/udb-web.service`
- `deploy/nginx-udb-web.conf`

Базовые шаги:

1. Склонировать проект в `/root/UDB_bot` (или в свой путь, но тогда поправить пути в unit-файле).
2. Создать venv и установить зависимости (`requirements-web.txt`).
3. Создать `/root/UDB_bot/.env.web`.
4. Скопировать `deploy/udb-web.service` в `/etc/systemd/system/`.
5. `sudo systemctl daemon-reload && sudo systemctl enable --now udb-web`.
6. Скопировать `deploy/nginx-udb-web.conf` в `/etc/nginx/sites-available/`, включить сайт.
7. `sudo nginx -t && sudo systemctl reload nginx`.
8. Выпустить HTTPS сертификат через certbot.

Быстрый вариант установки при текущем пути `/root/UDB_bot`:

```bash
cd /root/UDB_bot
cp deploy/udb-web.service /etc/systemd/system/udb-web.service
systemctl daemon-reload
systemctl enable --now udb-web
systemctl status udb-web --no-pager
```

### Скрипты запуска/остановки

В репозитории есть:

- `start_bot.sh` — запускает бота (`main.py`) и `udb-web.service`.
- `stop_bot.sh` — останавливает бота и `udb-web.service`.

На сервере один раз дать права:

```bash
cd /root/UDB_bot
chmod +x start_bot.sh stop_bot.sh
```

Использование:

```bash
./start_bot.sh
./stop_bot.sh
```

## Daily AI Summary Pipeline

Добавлен обмен через Google Sheets для ежедневных саммари. Локальные JSON-файлы в папке `ai_exchange/` сохраняются как отладочный снимок экспорта:

- `yyyy_mm_dd_<chat_id>_chatlog.json` — экспорт сообщений окна `23:20(вчера) -> 23:20(сегодня)` по локальному времени сервера.
- `yyyy_mm_dd_<chat_id>_summary.json` — результат агента.

В `main.py` запущены фоновые задачи:

- `23:20` — экспорт chatlog в Google Sheets.
- `23:59` — чтение summary и публикация в соответствующий `chat_id`.

## Google Sheets Exchange (recommended)

Обмен через Google Sheets:

- Бот пишет лог в лист `log` (перезаписывает только текущий день).
- Automation читает `log` и пишет саммари в лист `summary`.
- Бот в `23:59` читает `summary` и публикует в чат.

### Env переменные

```bash
UDB_SHEETS_ENABLED=1
UDB_SHEETS_ID=1LpC-l0AFgraofQzjHb165B_08p5Bs4q4P3-Vex9R-FY
UDB_SHEETS_LOG_SHEET=log
UDB_SHEETS_SUMMARY_SHEET=summary
GOOGLE_SERVICE_ACCOUNT_FILE=/root/UDB_bot/google-service-account.json
```

Опциональные таймауты для защиты фоновой задачи от зависаний:

```bash
UDB_CHATLOG_EXPORT_TIMEOUT_SECONDS=300
UDB_SHEETS_HTTP_TIMEOUT_SECONDS=30
```

Для per-request таймаута Google Sheets установите также `google-auth-httplib2` и `httplib2`; без них общий таймаут фоновой задачи всё равно сохранит отзывчивость бота.

### Формат листов

- `log` header:
  - `date_key | chat_id | author | text | message_datetime | window_start | window_end`
- `summary` header:
  - `date_key | chat_id | bullet_order | bullet_text`

Подробное ТЗ для automation: `ai_exchange/AUTOMATION_SPEC_GOOGLE_SHEETS.md`.

### Python зависимости (бот)

```bash
pip install google-api-python-client google-auth google-auth-httplib2 httplib2
```

## Text2SQL MVP через локальную Ollama

Команда `/db текст запроса` создаёт задачу `text_to_sql`; локальный `ai_worker.py` забирает её с VPS, отправляет prompt в Ollama и возвращает SQL `SELECT` в backend. Backend валидирует SQL, выполняет его read-only в `stats.db` и отвечает в Telegram reply на исходное сообщение.

Команда `/profile_update` доступна только `ADMIN_IDS`: она сразу отвечает `Обновление запущено` и ставит в очередь `profile_update` для текущего чата за предыдущий календарный день. Ночной scheduler делает то же для всех чатов каждый день в 01:00 по локальному времени сервера. Worker остаётся тем же: backend передаёт prompt, worker возвращает JSON, backend валидирует его и сохраняет дневной профиль в закрытое хранилище профилей, недоступное для `/db`.

Фоновая задача `chat_summary` примерно раз в 2-4 часа сжимает новые сообщения группового чата в короткое саммари до 150 символов и сохраняет его в `ai_summary`. Это низкоприоритетная AI-задача: она создаётся только когда нет более важных AI-задач, но не откладывается дольше 4 часов.

Задача `response` генерирует живые ответы бота в чат: случайно с шансом из `/settings` (по умолчанию 3%, максимум 10%) или после LLM-классификации прямого обращения к боту. Прямые обращения (`reply`, `@udb_flood_bot`, слово "бот") сначала попадают в быструю очередь `ai_type_checks`; классификатор выбирает `response`, `text_to_sql`, `web_search` или `ignore`. Для `web_search` быстрый worker сначала строит `search_plan`, backend ищет snippets в локальном SearXNG и создаёт обычную `response` задачу с веб-контекстом. Ответ строится на последних сообщениях, последних саммари и профиле автора.

### VPS env

В `/root/UDB_bot/.env.web` должен быть общий секрет для worker:

```bash
AI_WORKER_TOKEN=change_me_to_random_worker_secret
SEARXNG_URL=http://127.0.0.1:8888
WEB_SEARCH_MAX_RESULTS=8
WEB_SEARCH_TIMEOUT_SECONDS=8
```

После изменения env перезапустить web-сервис:

```bash
systemctl restart udb-web
```

SearXNG для web-поиска поднимается на VPS как localhost-only сервис из `deploy/searxng/docker-compose.yml`; JSON output должен быть включён в `settings.yml`.

### Локальный worker Windows

1. Скопировать `ai_worker.local.example.bat` в `ai_worker.local.bat`.
2. Вписать тот же `AI_WORKER_TOKEN`, что на VPS.
3. Убедиться, что Ollama доступна на `http://localhost:11434` и модель `gemma4:e4b` установлена.
4. Запустить `run_ai_worker.bat`: он откроет основной `ai_worker.py` и быстрый `ai_classifier_worker.py`.
