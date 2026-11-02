#!/usr/bin/env bash
# Pull the latest code from GitHub and restart the app with it.
#   ./scripts/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Getting the latest code from GitHub"
git pull --ff-only

# Hyper-V's Default Switch can hand the VM a new IP after a reboot.
# Make sure every current IP is allowed, or Django answers "Bad Request (400)".
for ip in $(hostname -I | tr ' ' '\n' | grep -E '^[0-9]+\.'); do
  if ! grep -q "^DJANGO_ALLOWED_HOSTS=.*\b${ip}\b" .env; then
    sed -i "s|^DJANGO_ALLOWED_HOSTS=\(.*\)|DJANGO_ALLOWED_HOSTS=\1,${ip}|" .env
    echo "Added new VM address ${ip} to DJANGO_ALLOWED_HOSTS"
  fi
done

# Bank details are encrypted with FIELD_ENCRYPTION_KEY (added 2 Nov 2026).
if ! grep -q "^FIELD_ENCRYPTION_KEY=" .env || grep -q "^FIELD_ENCRYPTION_KEY=change-me" .env; then
  sed -i "/^FIELD_ENCRYPTION_KEY=/d" .env
  echo "FIELD_ENCRYPTION_KEY=$(openssl rand -base64 32 | tr '+/' '-_')" >> .env
  echo "Added FIELD_ENCRYPTION_KEY to .env. Back up .env somewhere safe: bank details can't be read without it."
fi

echo "==> Rebuilding and restarting"
sudo docker compose up -d --build
sudo docker compose ps
echo "Done. Open http://$(hostname -I | awk '{print $1}'):8000/"
