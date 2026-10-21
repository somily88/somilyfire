#!/usr/bin/env bash
# تحديث البوت على الخادم: يسحب آخر نسخة من GitHub ثم يعيد البناء والتشغيل
# الاستخدام:  cd /opt/crypto-bot && bash update.sh
set -e
git pull --ff-only
if docker compose version >/dev/null 2>&1; then
  docker compose up -d --build
else
  docker-compose up -d --build
fi
docker image prune -f >/dev/null 2>&1 || true
echo "تم التحديث. تابع اللوج:  docker logs -f telegram_crypto_bot"
