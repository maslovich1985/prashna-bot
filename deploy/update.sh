#!/usr/bin/env bash
# Обновление кода бота на VPS: bash /opt/prashna-bot/deploy/update.sh /путь/к/новой/версии
set -euo pipefail
APP_DIR=/opt/prashna-bot
SRC=${1:-.}
systemctl stop prashna-bot
cp -r "$SRC"/app "$SRC"/run.py "$SRC"/requirements.txt "$SRC"/deploy "$APP_DIR"/
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q
cp "$APP_DIR/deploy/prashna-bot.service" /etc/systemd/system/prashna-bot.service
chown -R prashna:prashna "$APP_DIR"
systemctl daemon-reload
systemctl start prashna-bot
systemctl --no-pager status prashna-bot | head -n 12
