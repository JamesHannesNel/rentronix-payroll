#!/bin/sh
# Runs inside the web container before the app starts.
set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput >/dev/null

exec "$@"
