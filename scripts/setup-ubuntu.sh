#!/usr/bin/env bash
# One-time setup of the Rentronix payroll app on an Ubuntu VM (22.04 or 24.04).
# Run from inside the cloned repository:   ./scripts/setup-ubuntu.sh
# Safe to run again: it skips anything already done.
set -euo pipefail

cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"
say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your normal user (it uses sudo when needed), not as root." >&2
  exit 1
fi

say "Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y -qq git curl ufw openssh-server ca-certificates openssl >/dev/null

if ! command -v docker >/dev/null 2>&1; then
  say "Installing Docker"
  if ! sudo apt-get install -y -qq docker.io docker-compose-v2 >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sudo sh
  fi
fi
if ! docker compose version >/dev/null 2>&1 && ! sudo docker compose version >/dev/null 2>&1; then
  sudo apt-get install -y -qq docker-compose-v2 >/dev/null || sudo apt-get install -y -qq docker-compose-plugin >/dev/null
fi
sudo systemctl enable --now docker >/dev/null
if ! id -nG "$USER" | grep -qw docker; then
  sudo usermod -aG docker "$USER"
  echo "Added $USER to the docker group (takes effect at your next login)."
fi

say "Creating .env (settings and secrets for this server)"
if [ ! -f .env ]; then
  cp .env.example .env
  ips="$(hostname -I | tr ' ' '\n' | grep -E '^[0-9]+\.' | paste -sd, -)"
  sed -i "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=$(openssl rand -hex 32)|" .env
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 24)|" .env
  sed -i "s|^DJANGO_ALLOWED_HOSTS=.*|DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,${ips}|" .env
  chmod 600 .env
  echo "Created .env. PaySpace is in mock mode until you add staging credentials."
else
  echo ".env already exists; leaving it alone."
fi

say "Firewall: allow SSH and the app (port 8000)"
sudo ufw allow OpenSSH >/dev/null
sudo ufw allow 8000/tcp >/dev/null
sudo ufw --force enable >/dev/null
sudo ufw status | sed -n '1,8p'

say "Building and starting the app (first build takes a few minutes)"
sudo docker compose up -d --build
echo "Waiting for the app to answer..."
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -fsS http://127.0.0.1:8000/healthz >/dev/null && echo "App is up." || {
  echo "The app did not start. Show the log with: sudo docker compose logs web" >&2; exit 1; }

say "Loading demo data from the mock PaySpace"
sudo docker compose exec -T web python manage.py payspace_link --payspace-company 900001 || true
sudo docker compose exec -T web python manage.py payspace_pull || true

if ! sudo docker compose exec -T web python manage.py shell -c \
  "from django.contrib.auth import get_user_model as g; raise SystemExit(0 if g().objects.filter(is_superuser=True).exists() else 1)"; then
  say "Create your admin login"
  sudo docker compose exec web python manage.py createsuperuser
fi

ip="$(hostname -I | awk '{print $1}')"
say "Done"
cat <<EOF
Open the admin from Windows:   http://${ip}:8000/admin/
Code is in:                    ${APP_DIR}
Update to the latest code:     ./scripts/update.sh
Back up the database:          ./scripts/backup.sh
EOF
