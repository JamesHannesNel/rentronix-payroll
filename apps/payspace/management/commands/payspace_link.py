"""Create (or show) the link between one of our companies and a PaySpace company.

    python manage.py payspace_link                      # list PaySpace companies you can link
    python manage.py payspace_link --payspace-company 900001
        # creates our company from the PaySpace name if needed and links it
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Company, audit
from apps.payspace.client import PaySpaceClient, PaySpaceError
from apps.payspace.models import PaySpaceLink


class Command(BaseCommand):
    help = "Link a PaySpace company to a company in this app."

    def add_arguments(self, parser):
        parser.add_argument("--payspace-company", help="PaySpace company ID to link.")
        parser.add_argument("--name", help="Name for our company (default: PaySpace's company name).")

    def handle(self, *args, **options):
        try:
            available = PaySpaceClient.from_settings().companies()
        except PaySpaceError as exc:
            raise CommandError(str(exc)) from exc

        if not options["payspace_company"]:
            self.stdout.write("PaySpace companies you can link:")
            for c in available:
                linked = PaySpaceLink.objects.filter(payspace_company_id=str(c.get("company_id"))).first()
                note = f"  (linked to {linked.company})" if linked else ""
                self.stdout.write(f"  {c.get('company_id')}  {c.get('company_name')}{note}")
            self.stdout.write("\nRun again with --payspace-company <id> to link one.")
            return

        ps_id = str(options["payspace_company"])
        match = next((c for c in available if str(c.get("company_id")) == ps_id), None)
        if match is None:
            raise CommandError(f"PaySpace company {ps_id} is not available to these credentials.")

        name = options["name"] or match.get("company_name") or f"PaySpace company {ps_id}"
        with transaction.atomic():
            company, _ = Company.objects.get_or_create(name=name)
            link, created = PaySpaceLink.objects.update_or_create(
                company=company,
                defaults={"payspace_company_id": ps_id, "payspace_company_name": match.get("company_name", "")},
            )
            audit("payspace.link", actor="system:payspace_link", company=company, target=f"PaySpace {ps_id}")
        verb = "Linked" if created else "Updated link for"
        self.stdout.write(self.style.SUCCESS(f"{verb} {company} ↔ PaySpace {ps_id}. Next: python manage.py payspace_pull"))
