#!/usr/bin/env bash
# Применяет код, залитый в /opt/prashna-bot/incoming, и перезапускает бота.
# Вызывается из GitHub Actions по SSH. Запускается от root (через sudo).
set -euo pipefail

APP_DIR=/opt/prashna-bot
INCOMING="$APP_DIR/incoming"
BACKUP="$APP_DIR/backups/code-$(date +%F-%H%M%S)"

[ -d "$INCOMING/app" ] || { echo "Нет $INCOMING/app — деплой прерван"; exit 1; }

echo "==> Резервная копия текущего кода в $BACKUP"
mkdir -p "$BACKUP"
cp -r "$APP_DIR/app" "$APP_DIR/run.py" "$APP_DIR/requirements.txt" "$BACKUP/" 2>/dev/null || true

echo "==> Обновление файлов"
rm -rf "$APP_DIR/app"
cp -r "$INCOMING/app" "$APP_DIR/app"
cp "$INCOMING/run.py" "$INCOMING/requirements.txt" "$APP_DIR/"
cp -r "$INCOMING/deploy" "$APP_DIR/"
chmod +x "$APP_DIR/deploy"/*.sh

echo "==> Зависимости"
# /tmp на этом VPS — tmpfs на 479 МБ, сборке пакетов его не хватает
mkdir -p "$APP_DIR/tmp"
TMPDIR="$APP_DIR/tmp" "$APP_DIR/.venv/bin/pip" install --no-cache-dir -q -r "$APP_DIR/requirements.txt"
rm -rf "$APP_DIR/tmp"/*

echo "==> Права и unit-файл"
chown -R prashna:prashna "$APP_DIR"
cp "$APP_DIR/deploy/prashna-bot.service" /etc/systemd/system/prashna-bot.service
systemctl daemon-reload

echo "==> Перезапуск"
systemctl restart prashna-bot
sleep 4

if systemctl is-active --quiet prashna-bot; then
    echo "==> Бот работает"
    # старые копии кода держим 30 дней
    find "$APP_DIR/backups" -maxdepth 1 -name 'code-*' -type d -mtime +30 -exec rm -rf {} + 2>/dev/null || true
else
    echo "!!! Бот не поднялся, откат на $BACKUP"
    rm -rf "$APP_DIR/app"
    cp -r "$BACKUP/app" "$APP_DIR/app"
    cp "$BACKUP/run.py" "$APP_DIR/" 2>/dev/null || true
    chown -R prashna:prashna "$APP_DIR"
    systemctl restart prashna-bot
    echo "--- последние логи ---"
    journalctl -u prashna-bot -n 30 --no-pager
    exit 1
fi
