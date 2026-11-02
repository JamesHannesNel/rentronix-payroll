# Rentronix Payroll

Time, attendance and payroll for Rentronix clients.
Open `http://<vm address>:8000/` and sign in. The everyday screens are:

| Screen | What it's for |
| --- | --- |
| Today | Who's on which site right now (the site board), and anything that needs checking |
| Attendance | One day at a time: first in, last out, hours, and days with a missing clock |
| Employees | Everyone, with site, biometric enrolment and whether bank details are captured |
| Pay runs | Net pay in, checked, approved, then the FNB upload file out |
| Clock readers | Which readers have been heard from and when |

Setup (companies, users, bank accounts, API keys, PaySpace links) is under
**Setup and settings**, which opens the admin at `/admin/`.

Users see only the companies they're members of. Owners and payroll
administrators see pay runs; supervisors and viewers don't.

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
| Fill the mock company with demo clockings | `sudo docker compose exec web python manage.py demo_clocks` |
| Issue a Bio3G API key | `sudo docker compose exec web python manage.py issue_api_key --company "Demo" --name Bio3G` |

## Paying staff through FNB

The app builds the payment file FNB Online Banking imports, so net pay is
never typed into FNB by hand.

1. **Once per company:** add the paying account under *Payments (FNB) →
   Company paying accounts*, and each employee's bank details on their
   employee page (or in bulk: `import_bank_details`, below). Account numbers
   are stored encrypted.
2. **Each pay run:** *Payment batches → Add*. Pick the company, name the
   batch (e.g. "Fortnight 1–15 Nov") and the action date, then **Save and
   continue editing**.
3. Click **Add active employees**, then **Import amounts (CSV)** with the
   net pay report from PaySpace (two columns: employee code, net pay). You
   can also type amounts into the lines.
4. Fix anything listed under **Check**, click **Remove R0.00 lines** for
   people not being paid, then **Approve**. The batch locks.
5. Click **Download FNB file**. In FNB: *Payments → Import*, choose the
   **.CSV** file type, upload, then review and authorise the batch in FNB as
   usual. No money moves until it is authorised in FNB.

The first time, check FNB's import results report against the batch total
before authorising.

Bulk bank details (heading row required; branch code optional):

```bash
sudo docker compose exec -T web python manage.py import_bank_details --company "Demo" < bank.csv
# employee_code,bank,account_number,account_type,branch_code
# EMP0001,capitec,1234567890,savings,
```

Banks: absa, african_bank, bidvest, capitec, discovery, fnb, investec,
nedbank, standard_bank, tymebank, other. Delete the CSV afterwards.

**Back up `.env`.** It holds `FIELD_ENCRYPTION_KEY`; without it, stored
bank details cannot be read.

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
| `apps/payments` | Bank details (encrypted), payment batches, FNB import file |
| `apps/web` | The everyday screens: today, attendance, employees, pay runs, readers |
| `docs/INTEGRATION-API.md` | The API as described to Bio3G |
| `scripts/` | VM setup, update and backup |
| `tests/` | Automated tests (run on GitHub with every push) |

## Developing

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
DJANGO_DEBUG=true pytest
```
