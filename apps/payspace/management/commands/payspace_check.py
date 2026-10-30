"""Phase 0 connection test: can we sign in to PaySpace and read data?

    docker compose exec web python manage.py payspace_check
    docker compose exec web python manage.py payspace_check --company 12345

Prints counts and field NAMES only. It never prints employee values, so
the output is safe to paste into a chat or a ticket.
"""

import datetime as dt

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.payspace.client import PaySpaceClient, PaySpaceError


class Command(BaseCommand):
    help = "Test the PaySpace connection and show what data is available (no personal data is printed)."

    def add_arguments(self, parser):
        parser.add_argument("--company", help="PaySpace company ID to inspect (default: the first one available).")

    def handle(self, *args, **options):
        env = settings.PAYSPACE["ENV"]
        self.stdout.write(f"PaySpace environment: {env}")
        try:
            client = PaySpaceClient.from_settings()
            companies = client.companies()
        except PaySpaceError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("Signed in to PaySpace."))

        if not companies:
            raise CommandError("Signed in, but these credentials can see no companies. Ask PaySpace to link one.")
        self.stdout.write(f"\nCompanies these credentials can reach ({len(companies)}):")
        for company in companies:
            self.stdout.write(f"  {company.get('company_id')}  {company.get('company_name') or company.get('company_code')}")

        company_id = options["company"] or companies[0].get("company_id")
        self.stdout.write(f"\nInspecting company {company_id}:")
        checks = [
            ("Employees", lambda: client.employees(company_id, dt.date.today())),
            ("Employment statuses", lambda: client.employment_statuses(company_id)),
            ("Positions", lambda: client.positions(company_id)),
            ("Pay frequencies", lambda: client.pay_frequencies(company_id)),
            ("Component codes", lambda: client.component_codes(company_id)),
            ("Pay runs", lambda: client.company_runs(company_id)),
        ]
        failures = 0
        for label, fetch in checks:
            try:
                rows = fetch()
            except PaySpaceError as exc:
                failures += 1
                self.stdout.write(self.style.ERROR(f"  {label}: FAILED – {exc}"))
                continue
            fields = sorted(rows[0].keys()) if rows else []
            self.stdout.write(self.style.SUCCESS(f"  {label}: {len(rows)} rows"))
            if fields:
                self.stdout.write(f"    fields: {', '.join(fields)}")

        if failures:
            raise CommandError(f"{failures} check(s) failed. Copy this output to Claude to fix the mapping.")
        self.stdout.write(self.style.SUCCESS("\nAll checks passed."))
