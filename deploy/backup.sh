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

echo "Бэкап готов: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"
