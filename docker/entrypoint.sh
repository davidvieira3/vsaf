#!/bin/sh
set -e

if ! ls /app/inventory/migrations/0001_*.py >/dev/null 2>&1; then
  echo "ERREUR : migrations absentes. Lancez une fois 'make migrations' puis reconstruisez l'image (voir README)." >&2
  [ "$1" = "makemigrations" ] || exit 1
fi

case "$1" in
  web)
    python manage.py migrate --noinput
    python manage.py bootstrap
    exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 \
      --workers "${WEB_WORKERS:-4}" --timeout 180 --access-logfile - --forwarded-allow-ips="*"
    ;;
  worker)
    exec celery -A config worker --loglevel INFO --concurrency "${COLLECT_CONCURRENCY:-16}" \
      --max-tasks-per-child 20 --hostname "worker@%h"
    ;;
  beat)
    exec celery -A config beat --loglevel INFO --schedule /tmp/celerybeat-schedule
    ;;
  makemigrations)
    exec python manage.py makemigrations accounts core inventory
    ;;
  *)
    exec "$@"
    ;;
esac
