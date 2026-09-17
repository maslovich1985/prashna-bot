#!/usr/bin/env bash
# Установка прашна-бота на чистый Ubuntu/Debian VPS (Beget и любой другой).
# Запускать от root:  bash deploy/install.sh
set -euo pipefail

APP_DIR=/opt/prashna-bot
APP_USER=prashna
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> 1/7 Пакеты системы"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-dev build-essential \
    ca-certificates curl tzdata sqlite3 ufw

echo "==> 2/7 Пользователь $APP_USER"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"

echo "==> 3/7 Файлы приложения в $APP_DIR"
mkdir -p "$APP_DIR"
if [ "$SRC_DIR" != "$APP_DIR" ]; then
    cp -r "$SRC_DIR"/app "$SRC_DIR"/run.py "$SRC_DIR"/requirements.txt "$SRC_DIR"/deploy "$APP_DIR"/
    [ -f "$SRC_DIR/.env.example" ] && cp -n "$SRC_DIR/.env.example" "$APP_DIR"/.env.example
fi
mkdir -p "$APP_DIR/data"

echo "==> 4/7 Виртуальное окружение и зависимости"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip wheel -q
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

echo "==> 5/7 Файл .env"
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo "    ! Заполните $APP_DIR/.env (TELEGRAM_TOKEN и GROQ_API_KEY), затем перезапустите сервис."
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"

echo "==> 6/7 systemd"
cp "$APP_DIR/deploy/prashna-bot.service" /etc/systemd/system/prashna-bot.service
systemctl daemon-reload
systemctl enable prashna-bot

echo "==> 7/7 Firewall (только SSH; боту нужен лишь исходящий трафик)"
ufw allow OpenSSH >/dev/null 2>&1 || true
ufw --force enable >/dev/null 2>&1 || true

echo
echo "Готово. Дальше:"
echo "  nano $APP_DIR/.env            # вписать токены"
echo "  systemctl start prashna-bot"
echo "  journalctl -u prashna-bot -f  # смотреть логи"
