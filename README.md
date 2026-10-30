# Rentronix Payroll

Time, attendance and payroll for Rentronix clients, modelled on .
Phase 0: companies, employees pulled from PaySpace, and the integration API
that Biometrics 3G readers will post clocks to. Everything is managed
through the admin at `/admin/` for now.

New to GitHub? Start with [docs/GITHUB-BASICS.md](docs/GITHUB-BASICS.md).

## Set up the Ubuntu VM (once)

Run these in the VM's terminal, as your normal user.

**1. Give the VM read access to this repository (a deploy key).**

```bash
sudo apt-get update && sudo apt-get install -y git
ssh-keygen -t ed25519 -f ~/.ssh/github_payroll -N "" -C "payroll-vm"
cat ~/.ssh/github_payroll.pub
```

Copy the line that starts with `ssh-ed25519`. On GitHub, open the repository →
**Settings → Deploy keys → Add deploy key**. Title: `Ubuntu VM`, paste the key,
leave "Allow write access" **unticked**, then click **Add key**.

**2. Download the code.** Replace `<your-github-username>`.

```bash
cd ~
GIT_SSH_COMMAND="ssh -i ~/.ssh/github_payroll -o StrictHostKeyChecking=accept-new" \
  git clone git@github.com:<your-github-username>/rentronix-payroll.git
cd rentronix-payroll
git config core.sshCommand "ssh -i ~/.ssh/github_payroll"
```

**3. Install and start everything.**

```bash
./scripts/setup-ubuntu.sh
```

It installs Docker, creates `.env` with random passwords, opens the firewall
for SSH and port 8000, starts the app, loads demo data from the mock
PaySpace, and asks you to create your admin login. At the end it prints the
address to open from Windows, e.g. `http://172.17.244.225:8000/admin/`.

## Everyday commands (on the VM, in `~/rentronix-payroll`)

| Task | Command |
| --- | --- |
| Get the latest code and restart | `./scripts/update.sh` |
| Back up the database | `./scripts/backup.sh` |
| See if it's running | `sudo docker compose ps` |
| Watch the app's log | `sudo docker compose logs -f web` |
| Test the PaySpace connection | `sudo docker compose exec web python manage.py payspace_check` |
| Pull from PaySpace | `sudo docker compose exec web python manage.py payspace_pull` |
| Issue a Bio3G API key | `sudo docker compose exec web python manage.py issue_api_key --company "Demo" --name Bio3G` |

## Switching from mock PaySpace to staging

When you have the staging credentials, edit `.env` on the VM (`nano .env`):

```
PAYSPACE_ENV=staging
PAYSPACE_CLIENT_ID=...
PAYSPACE_CLIENT_SECRET=...
```

Then `sudo docker compose up -d` and run `payspace_check`. It prints counts
and field names only, never employee details, so its output is safe to
share with Claude to finish the field mapping.

## Hyper-V note

The Default Switch gives the VM a new IP address after some reboots. If the
admin page shows "Bad Request (400)", run `./scripts/update.sh`, which adds the new
address to `.env`.

## Layout

| Path | What's there |
| --- | --- |
| `apps/core` | Companies, user access, API keys, audit log |
| `apps/employees` | Employees, sites, teams |
| `apps/payspace` | PaySpace API client, mock PaySpace, pull sync, commands |
| `apps/timeclock` | Devices, clock records, the integration API |
| `docs/INTEGRATION-API.md` | The API as described to Bio3G |
| `scripts/` | VM setup, update and backup |
| `tests/` | Automated tests (run on GitHub with every push) |

## Developing

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
DJANGO_DEBUG=true pytest
```
