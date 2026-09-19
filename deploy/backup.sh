#!/usr/bin/env bash
# Резервная копия базы: bash /opt/prashna-bot/deploy/backup.sh
# Вызывается вручную и по таймеру prashna-backup.timer (ежедневно ~03:30).
set -euo pipefail

APP_DIR=/opt/prashna-bot
DB="$APP_DIR/data/prashna.sqlite3"
DEST="$APP_DIR/backups"
KEEP_DAYS=14
STAMP="$(date +%F-%H%M)"
SNAPSHOT="$DEST/prashna-$STAMP.sqlite3"

[ -f "$DB" ] || { echo "Нет базы $DB — бэкап прерван" >&2; exit 1; }
mkdir -p "$DEST"

# Оборванный снимок не должен оставаться в backups: под find по возрасту он не
# попадёт ещё 14 дней и будет выглядеть как нормальная копия.
trap 'rm -f "$SNAPSHOT"' ERR

# .backup, а не cp: бот пишет в базу в режиме WAL, копия файла на ходу может
# оказаться несогласованной, а онлайн-бэкап sqlite3 отдаёт целостный снимок.
sqlite3 "$DB" ".backup '$SNAPSHOT'"

# Проверяем снимок до сжатия: битый бэкап хуже отсутствующего — он создаёт
# ложное чувство защищённости и обнаруживается только при восстановлении.
if ! sqlite3 "$SNAPSHOT" 'PRAGMA integrity_check;' | grep -qx 'ok'; then
    rm -f "$SNAPSHOT"
    echo "Снимок не прошёл integrity_check — бэкап прерван" >&2
    exit 1
fi

gzip -9 -f "$SNAPSHOT"
ARCHIVE="$SNAPSHOT.gz"

find "$DEST" -maxdepth 1 -name 'prashna-*.sqlite3.gz' -mtime "+$KEEP_DAYS" -delete
# Несжатые снимки от старых версий скрипта: подчищаем, чтобы не занимали место
find "$DEST" -maxdepth 1 -name 'prashna-*.sqlite3' -mtime "+$KEEP_DAYS" -delete

SIZE="$(du -h "$ARCHIVE" | cut -f1)"
echo "Бэкап готов: $ARCHIVE ($SIZE)"

# ---------------------------- выгрузка за пределы VPS ---------------------- #
# Копия на том же диске не защищает от потери диска. Отправляем архив в закрытый
# Telegram-чат тем же ботом: нулевые зависимости и отдельный от VPS носитель.
# BACKUP_CHAT_ID — закрытый канал или личка владельца, разницы для скрипта нет.
# Переменные берём из .env, если скрипт запущен не через systemd-юнит.
if [ -z "${TELEGRAM_TOKEN:-}" ] && [ -f "$APP_DIR/.env" ]; then
    # shellcheck disable=SC1091
    set -a; . "$APP_DIR/.env"; set +a
fi

if [ -z "${BACKUP_CHAT_ID:-}" ]; then
    echo "BACKUP_CHAT_ID не задан — архив остаётся только на VPS" >&2
    exit 0
fi
[ -n "${TELEGRAM_TOKEN:-}" ] || { echo "Нет TELEGRAM_TOKEN — выгрузка невозможна" >&2; exit 1; }

# Лимит Bot API на документ — 50 МБ. Молча упереться в него нельзя: выгрузка
# перестанет работать ровно тогда, когда база станет ценной.
BYTES="$(stat -c %s "$ARCHIVE")"
if [ "$BYTES" -gt 50000000 ]; then
    echo "Архив $SIZE больше лимита Bot API в 50 МБ — нужен rclone в S3 (§4.4)" >&2
    exit 1
fi

HOST="$(hostname)"
CAPTION="Бэкап $HOST от $(date '+%d.%m.%Y %H:%M %Z'), $SIZE"

HTTP_CODE="$(curl -sS --max-time 300 --retry 3 --retry-delay 10 \
    -o /tmp/prashna-backup-upload.json -w '%{http_code}' \
    -F "chat_id=$BACKUP_CHAT_ID" \
    -F "caption=$CAPTION" \
    -F "document=@$ARCHIVE" \
    "https://api.telegram.org/bot$TELEGRAM_TOKEN/sendDocument" || echo 000)"

if [ "$HTTP_CODE" != "200" ]; then
    # Тело ответа Telegram содержит описание ошибки, но не токен — печатать безопасно
    echo "Выгрузка в Telegram не удалась (HTTP $HTTP_CODE): $(cat /tmp/prashna-backup-upload.json 2>/dev/null)" >&2
    rm -f /tmp/prashna-backup-upload.json
    exit 1
fi

rm -f /tmp/prashna-backup-upload.json
echo "Архив выгружен в чат $BACKUP_CHAT_ID"
