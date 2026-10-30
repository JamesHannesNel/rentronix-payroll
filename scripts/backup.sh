#!/usr/bin/env bash
# Dump the database to backups/payroll-YYYYmmdd-HHMM.sql.gz and keep the last 14.
#   ./scripts/backup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
chmod 700 backups
file="backups/payroll-$(date +%Y%m%d-%H%M).sql.gz"
sudo docker compose exec -T db pg_dump -U payroll payroll | gzip > "$file"
chmod 600 "$file"
ls -1t backups/payroll-*.sql.gz | tail -n +15 | xargs -r rm --
echo "Saved $file ($(du -h "$file" | cut -f1))"
