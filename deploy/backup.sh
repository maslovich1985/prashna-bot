#!/usr/bin/env bash
# Резервная копия базы: bash /opt/prashna-bot/deploy/backup.sh
set -euo pipefail
DB=/opt/prashna-bot/data/prashna.sqlite3
DEST=/opt/prashna-bot/backups
mkdir -p "$DEST"
sqlite3 "$DB" ".backup '$DEST/prashna-$(date +%F-%H%M).sqlite3'"
find "$DEST" -name 'prashna-*.sqlite3' -mtime +14 -delete
echo "Бэкап готов: $DEST"
