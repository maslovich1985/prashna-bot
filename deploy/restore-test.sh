#!/usr/bin/env bash
# Учебное восстановление из бэкапа: bash /opt/prashna-bot/deploy/restore-test.sh [архив.gz]
#
# Без аргумента берёт самый свежий архив из backups/. Разворачивает копию в отдельный
# каталог и НИЧЕГО не трогает в рабочей базе: смысл проверки в том, чтобы убедиться,
# что из архива действительно можно подняться, а не в том, чтобы что-то починить.
set -euo pipefail

APP_DIR=/opt/prashna-bot
DEST="$APP_DIR/restore-test"
ARCHIVE="${1:-}"

if [ -z "$ARCHIVE" ]; then
    ARCHIVE="$(find "$APP_DIR/backups" -maxdepth 1 -name 'prashna-*.sqlite3.gz' -printf '%T@ %p\n' \
        | sort -rn | head -1 | cut -d' ' -f2-)"
    [ -n "$ARCHIVE" ] || { echo "В $APP_DIR/backups нет архивов" >&2; exit 1; }
    echo "Архив не указан, берём самый свежий: $ARCHIVE"
fi
[ -f "$ARCHIVE" ] || { echo "Нет архива $ARCHIVE" >&2; exit 1; }

rm -rf "$DEST"
mkdir -p "$DEST"
DB="$DEST/prashna.sqlite3"

echo "==> 1/4 Распаковка"
gzip -dc "$ARCHIVE" > "$DB"

echo "==> 2/4 Целостность"
CHECK="$(sqlite3 "$DB" 'PRAGMA integrity_check;')"
[ "$CHECK" = "ok" ] || { echo "integrity_check: $CHECK" >&2; exit 1; }
echo "integrity_check: ok"

echo "==> 3/4 Схема"
# Таблицы перечисляем явно: пустой список при «успешном» распаковывании мусора
# выглядел бы как рабочая база.
for t in users prashna usage geocache; do
    sqlite3 "$DB" "SELECT 1 FROM sqlite_master WHERE type='table' AND name='$t';" | grep -qx 1 \
        || { echo "В копии нет таблицы $t" >&2; exit 1; }
done
sqlite3 "$DB" "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;" | tr '\n' ' '
echo

echo "==> 4/4 Данные"
sqlite3 "$DB" <<'SQL'
.mode list
.separator ": "
SELECT 'пользователей', COUNT(*) FROM users;
SELECT 'прашн', COUNT(*) FROM prashna;
SELECT 'последний вопрос', COALESCE(MAX(asked_at), 'нет записей') FROM prashna;
SELECT 'записей о лимитах', COUNT(*) FROM usage;
SELECT 'геокэш', COUNT(*) FROM geocache;
SQL

echo
echo "Копия развёрнута: $DB"
echo "Рабочая база не затронута."
echo
echo "Запустить бота на этой копии (основной бот должен быть остановлен —"
echo "два long polling с одним токеном дают Telegram 409 Conflict):"
echo
echo "  systemctl stop prashna-bot"
echo "  cd $APP_DIR && DB_PATH=$DB .venv/bin/python run.py"
echo "  # Ctrl+C, затем systemctl start prashna-bot"
