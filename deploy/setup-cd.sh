#!/usr/bin/env bash
# Подготовка VPS к автодеплою из GitHub Actions. Запускать на сервере от root один раз:
#   bash /opt/prashna-bot/deploy/setup-cd.sh
set -euo pipefail

APP_DIR=/opt/prashna-bot
DEPLOY_USER=deployer

echo "==> Пакеты"
apt-get update -qq && apt-get install -y -qq rsync sudo

echo "==> Пользователь $DEPLOY_USER (под ним ходит GitHub Actions)"
id -u "$DEPLOY_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$DEPLOY_USER"

echo "==> Каталоги"
mkdir -p "$APP_DIR/incoming" "$APP_DIR/backups"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR/incoming"
chown -R prashna:prashna "$APP_DIR/backups"

echo "==> Право запускать ровно один скрипт через sudo, без пароля"
cat > /etc/sudoers.d/prashna-deploy <<EOF
$DEPLOY_USER ALL=(root) NOPASSWD: $APP_DIR/deploy/apply.sh
EOF
chmod 440 /etc/sudoers.d/prashna-deploy
visudo -c >/dev/null

echo "==> Право читать статус сервиса"
cat > /etc/sudoers.d/prashna-status <<EOF
$DEPLOY_USER ALL=(root) NOPASSWD: /usr/bin/systemctl is-active prashna-bot
EOF
chmod 440 /etc/sudoers.d/prashna-status

echo "==> SSH-ключ для GitHub Actions"
sudo -u "$DEPLOY_USER" mkdir -p "/home/$DEPLOY_USER/.ssh"
sudo -u "$DEPLOY_USER" chmod 700 "/home/$DEPLOY_USER/.ssh"
if [ ! -f "/home/$DEPLOY_USER/.ssh/github_actions" ]; then
    sudo -u "$DEPLOY_USER" ssh-keygen -t ed25519 -N "" -C "github-actions" \
        -f "/home/$DEPLOY_USER/.ssh/github_actions"
    sudo -u "$DEPLOY_USER" bash -c \
        "cat /home/$DEPLOY_USER/.ssh/github_actions.pub >> /home/$DEPLOY_USER/.ssh/authorized_keys"
    sudo -u "$DEPLOY_USER" chmod 600 "/home/$DEPLOY_USER/.ssh/authorized_keys"
fi

chmod +x "$APP_DIR/deploy"/*.sh 2>/dev/null || true

echo
echo "================================================================"
echo "Готово. Теперь скопируйте ПРИВАТНЫЙ ключ целиком (вместе со"
echo "строками BEGIN и END) в секрет SSH_PRIVATE_KEY на GitHub:"
echo "================================================================"
cat "/home/$DEPLOY_USER/.ssh/github_actions"
echo "================================================================"
echo "Остальные секреты:"
echo "  SSH_HOST = $(curl -s ifconfig.me 2>/dev/null || echo 'IP вашего сервера')"
echo "  SSH_USER = $DEPLOY_USER"
echo "  SSH_PORT = 22   (если меняли порт SSH — укажите свой)"
echo "================================================================"
