#!/bin/bash
set -euo pipefail

BOT_DIR="/root/UDB_bot"
BOT_FILE="main.py"
BOT_SERVICE="udb-bot"
WEB_SERVICE="udb-web"
LOG_FILE="$BOT_DIR/bot.log"

unit_exists() {
    systemctl cat "$1" >/dev/null 2>&1
}

start_bot() {
    if unit_exists "$BOT_SERVICE"; then
        systemctl restart "$BOT_SERVICE"
        sleep 1
        if systemctl is-active --quiet "$BOT_SERVICE"; then
            echo "Bot started (${BOT_SERVICE}.service)"
            return
        fi
        echo "Failed to start bot (${BOT_SERVICE}.service)"
        systemctl status "$BOT_SERVICE" --no-pager || true
        exit 1
    fi

    cd "$BOT_DIR"
    if ps -eo args= | grep -E "[p]ython(3)? .*$BOT_FILE" >/dev/null; then
        echo "Bot is already running"
        return
    fi

    nohup "$BOT_DIR/venv/bin/python" "$BOT_FILE" >> "$LOG_FILE" 2>&1 &
    sleep 1
    if ps -eo args= | grep -E "[p]ython(3)? .*$BOT_FILE" >/dev/null; then
        echo "Bot started"
    else
        echo "Failed to start bot"
        exit 1
    fi
}

start_web() {
    if ! unit_exists "$WEB_SERVICE"; then
        echo "Service ${WEB_SERVICE}.service not found; skipping web start."
        return
    fi

    systemctl restart "$WEB_SERVICE"
    sleep 1
    if systemctl is-active --quiet "$WEB_SERVICE"; then
        echo "Web server started (${WEB_SERVICE}.service)"
    else
        echo "Failed to start web server (${WEB_SERVICE}.service)"
        systemctl status "$WEB_SERVICE" --no-pager || true
        exit 1
    fi
}

start_bot
start_web

echo "Bot logs: tail -f $LOG_FILE"
echo "Web status: systemctl status ${WEB_SERVICE} --no-pager"
