#!/bin/bash
set -euo pipefail

BOT_FILE="main.py"
BOT_SERVICE="udb-bot"
WEB_SERVICE="udb-web"

unit_exists() {
    systemctl cat "$1" >/dev/null 2>&1
}

stop_bot() {
    if unit_exists "$BOT_SERVICE"; then
        if systemctl is-active --quiet "$BOT_SERVICE"; then
            systemctl stop "$BOT_SERVICE"
            echo "Bot stopped (${BOT_SERVICE}.service)"
        else
            echo "Bot is already stopped (${BOT_SERVICE}.service)"
        fi
        return
    fi

    local pids
    pids="$(ps -eo pid=,args= | awk -v bot="$BOT_FILE" '$0 ~ /[p]ython(3)?/ && $0 ~ bot {print $1}' || true)"
    if [[ -z "$pids" ]]; then
        echo "Bot is not running"
        return
    fi

    echo "Stopping bot, PID: $pids"
    kill $pids || true
    sleep 2

    local remaining
    remaining="$(ps -eo pid=,args= | awk -v bot="$BOT_FILE" '$0 ~ /[p]ython(3)?/ && $0 ~ bot {print $1}' || true)"
    if [[ -n "$remaining" ]]; then
        echo "Bot did not stop, killing: $remaining"
        kill -9 $remaining || true
    fi
    echo "Bot stopped"
}

stop_web() {
    if ! unit_exists "$WEB_SERVICE"; then
        echo "Service ${WEB_SERVICE}.service not found; skipping web stop."
        return
    fi

    if systemctl is-active --quiet "$WEB_SERVICE"; then
        systemctl stop "$WEB_SERVICE"
        echo "Web server stopped (${WEB_SERVICE}.service)"
    else
        echo "Web server is already stopped"
    fi
}

stop_bot
stop_web
