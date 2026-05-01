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

Добавлен файловый обмен для ежедневных саммари в папке `ai_exchange/`:

- `yyyy_mm_dd_<chat_id>_chatlog.json` — экспорт сообщений окна `23:30(вчера) -> 23:30(сегодня)` по локальному времени сервера.
- `yyyy_mm_dd_<chat_id>_summary.json` — результат агента.

В `main.py` запущены фоновые задачи:

- `23:30` — экспорт chatlog + `git add/commit/push` папки `ai_exchange/`.
- `23:55` — чтение summary и публикация в соответствующий `chat_id`.

Для подтягивания summary на VPS перед публикацией настройте cron:

```bash
50 23 * * * cd /root/UDB_bot && /usr/bin/git pull --ff-only >> /root/UDB_bot/summary_sync.log 2>&1
```

Шаблон лежит в `deploy/summary_git_pull.cron`.

### Push из бота в GitHub

Даже для публичного репозитория push требует авторизацию.  
Для non-interactive push из nightly-экспорта задайте переменные окружения процесса бота:

- `UDB_GIT_PUSH_TOKEN` — GitHub PAT (минимум права на push в репозиторий)
- `UDB_GIT_PUSH_USERNAME` — опционально, по умолчанию `x-access-token`

Пример для systemd environment file:

```bash
UDB_GIT_PUSH_TOKEN=ghp_xxx...
UDB_GIT_PUSH_USERNAME=x-access-token
```

## Google Sheets Exchange (recommended)

Вместо Git/email можно включить обмен через Google Sheets:

- Бот пишет лог в лист `log` (перезаписывает только текущий день).
- Automation читает `log` и пишет саммари в лист `summary`.
- Бот в `23:55` читает `summary` и публикует в чат.

### Env переменные

```bash
UDB_SHEETS_ENABLED=1
UDB_SHEETS_ID=1LpC-l0AFgraofQzjHb165B_08p5Bs4q4P3-Vex9R-FY
UDB_SHEETS_LOG_SHEET=log
UDB_SHEETS_SUMMARY_SHEET=summary
GOOGLE_SERVICE_ACCOUNT_FILE=/root/UDB_bot/google-service-account.json
```

### Формат листов

- `log` header:
  - `date_key | chat_id | author | text | message_datetime | window_start | window_end`
- `summary` header:
  - `date_key | chat_id | bullet_order | bullet_text`

Подробное ТЗ для automation: `ai_exchange/AUTOMATION_SPEC_GOOGLE_SHEETS.md`.

### Python зависимости (бот)

```bash
pip install google-api-python-client google-auth
```
