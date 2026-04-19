#!/bin/bash
set -euo pipefail

BOT_DIR="/root/UDB_bot"
BOT_FILE="main.py"
VENV_DIR="$BOT_DIR/venv"
LOG_FILE="$BOT_DIR/bot.log"
WEB_SERVICE="udb-web"

unit_exists() {
    systemctl cat "$WEB_SERVICE" >/dev/null 2>&1
}

start_bot() {
    cd "$BOT_DIR" || {
        echo "Не найдена директория $BOT_DIR"
        exit 1
    }

    if pgrep -f "python.*${BOT_FILE}" >/dev/null; then
        echo "Бот уже запущен"
        return
    fi

    if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
        echo "Не найдено виртуальное окружение: $VENV_DIR"
        exit 1
    fi

    source "$VENV_DIR/bin/activate"
    nohup python "$BOT_FILE" >> "$LOG_FILE" 2>&1 &
    sleep 1

    if pgrep -f "python.*${BOT_FILE}" >/dev/null; then
        echo "Бот запущен"
    else
        echo "Не удалось запустить бота"
        exit 1
    fi
}

start_web() {
    if ! unit_exists; then
        echo "Сервис ${WEB_SERVICE}.service не найден. Пропускаю запуск веб-сервера."
        return
    fi

    systemctl restart "$WEB_SERVICE"
    sleep 1

    if systemctl is-active --quiet "$WEB_SERVICE"; then
        echo "Веб-сервер запущен (${WEB_SERVICE}.service)"
    else
        echo "Не удалось запустить веб-сервер (${WEB_SERVICE}.service)"
        systemctl status "$WEB_SERVICE" --no-pager || true
        exit 1
    fi
}

start_bot
start_web

echo "Логи бота: tail -f $LOG_FILE"
echo "Статус веба: systemctl status ${WEB_SERVICE} --no-pager"
