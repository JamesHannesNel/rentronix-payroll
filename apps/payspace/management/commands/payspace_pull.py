"""Pull employees, pay frequencies and component codes from PaySpace.

    docker compose exec web python manage.py payspace_pull            # every linked company
    docker compose exec web python manage.py payspace_pull --company "Demo Construction"
"""

from django.core.management.base import BaseCommand, CommandError

from apps.payspace.client import PaySpaceClient, PaySpaceError
from apps.payspace.models import PaySpaceLink
from apps.payspace.sync import pull_company


class Command(BaseCommand):
    help = "Pull data from PaySpace for linked companies (read-only on the PaySpace side)."

    def add_arguments(self, parser):
        parser.add_argument("--company", help="Name (or part of it) of one of our companies.")

    def handle(self, *args, **options):
        links = PaySpaceLink.objects.filter(is_active=True, company__is_active=True).select_related("company")
        if options["company"]:
            links = links.filter(company__name__icontains=options["company"])
        if not links:
            raise CommandError("No active PaySpace links. Add one in the admin under PaySpace → PaySpace links, "
                               "or run: python manage.py payspace_link")

        try:
            client = PaySpaceClient.from_settings()
        except PaySpaceError as exc:
            raise CommandError(str(exc)) from exc

        failed = 0
        for link in links:
            try:
                result = pull_company(link, client)
            except PaySpaceError as exc:
                failed += 1
                self.stdout.write(self.style.ERROR(f"{link.company}: FAILED – {exc}"))
                continue
            counts = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in result.counts().items())
            self.stdout.write(self.style.SUCCESS(f"{link.company}: {counts}"))
            for warning in result.warnings:
                self.stdout.write(self.style.WARNING(f"  warning: {warning}"))
        if failed:
            raise CommandError(f"{failed} company pull(s) failed.")
