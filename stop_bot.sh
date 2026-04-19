#!/bin/bash
set -euo pipefail

BOT_FILE="main.py"
WEB_SERVICE="udb-web"

unit_exists() {
    systemctl cat "$WEB_SERVICE" >/dev/null 2>&1
}

stop_bot() {
    local pids
    pids="$(pgrep -f "python.*${BOT_FILE}" || true)"

    if [[ -z "$pids" ]]; then
        echo "Бот не запущен"
        return
    fi

    echo "Останавливаю бота, PID: $pids"
    kill $pids || true
    sleep 2

    local remaining
    remaining="$(pgrep -f "python.*${BOT_FILE}" || true)"
    if [[ -n "$remaining" ]]; then
        echo "Процесс не завершился, принудительная остановка: $remaining"
        kill -9 $remaining || true
    fi

    echo "Бот остановлен"
}

stop_web() {
    if ! unit_exists; then
        echo "Сервис ${WEB_SERVICE}.service не найден. Пропускаю остановку веб-сервера."
        return
    fi

    if systemctl is-active --quiet "$WEB_SERVICE"; then
        systemctl stop "$WEB_SERVICE"
        echo "Веб-сервер остановлен (${WEB_SERVICE}.service)"
    else
        echo "Веб-сервер уже остановлен"
    fi
}

stop_bot
stop_web
