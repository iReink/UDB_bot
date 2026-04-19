# UDB_bot

## Web MVP (FastAPI + Telegram auth)

В репозиторий добавлен отдельный веб-бэкенд и фронтенд для браузерного MVP:

- `web/server.py` — FastAPI API + Telegram Login auth + сессии.
- `web/templates/index.html` — страница с модалкой выбора `chat_id`.
- `web/static/*` — UI, переключение аккаунта в хедере, вывод баланса.

### Что делает MVP

1. Авторизация через Telegram Login Widget.
2. После входа открывается модальное окно со списком всех аккаунтов пользователя по паре `user_id;chat_id`.
3. После выбора показывается:
   `Вы вошли через чат <название чата>, ваш баланс <n> сит`
4. В правом верхнем углу доступен выпадающий список всех чатов/ЛС для быстрого переключения.

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
- `BOT_USERNAME` (без `@`)
- `WEB_SESSION_SECRET`

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
