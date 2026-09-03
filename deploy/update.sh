#!/usr/bin/env bash
# ChefPro (Oshxona) — serverga kod yangilash
# Ishlatish: ./deploy/update.sh
set -euo pipefail

SERVER="root@173.249.31.226"
APP_DIR="/var/www/chefpro"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ -z "${SSHPASS:-}" ]]; then
  echo "SSHPASS o'rnatilmagan. Masalan: export SSHPASS='...'"
  exit 1
fi

echo ">> Kod yuborilmoqda..."
sshpass -e rsync -avz --delete \
  -e "ssh -o StrictHostKeyChecking=no" \
  --exclude 'venv/' \
  --exclude '__pycache__/' \
  --exclude '.git/' \
  --exclude 'db.sqlite3' \
  --exclude 'config/settings_dev.py' \
  --exclude 'config/settings_prod.py' \
  --exclude 'staticfiles/' \
  --exclude '.env' \
  --exclude '.DS_Store' \
  "$LOCAL_DIR/" "$SERVER:$APP_DIR/"

echo ">> Migrate + static + restart..."
sshpass -e ssh -o StrictHostKeyChecking=no "$SERVER" bash <<REMOTE
set -euo pipefail
cd $APP_DIR
source venv/bin/activate
export DJANGO_SETTINGS_MODULE=config.settings_prod
pip install -r requirements.txt gunicorn whitenoise -q
python manage.py migrate --noinput
python manage.py collectstatic --noinput
systemctl restart chefpro.service
systemctl is-active chefpro.service
REMOTE

echo ">> Tayyor: https://chefpro.uz"
