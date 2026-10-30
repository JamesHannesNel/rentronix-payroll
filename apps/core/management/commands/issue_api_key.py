"""Issue an integration API key for one company and print it once.

    docker compose exec web python manage.py issue_api_key --company "Demo" --name Bio3G
"""

from django.core.management.base import BaseCommand, CommandError

from apps.core.models import ApiKey, Company, audit

DEFAULT_SCOPES = [ApiKey.Scope.EMPLOYEE_READ, ApiKey.Scope.CLOCK_WRITE, ApiKey.Scope.DEVICE_WRITE]


class Command(BaseCommand):
    help = "Create an API key for a partner such as Bio3G. The key is shown once only."

    def add_arguments(self, parser):
        parser.add_argument("--company", required=True, help="Name (or part of it) of the company.")
        parser.add_argument("--name", required=True, help="Who uses the key, e.g. Bio3G.")
        parser.add_argument("--scope", action="append", choices=ApiKey.Scope.values,
                            help="Repeat for several scopes. Default: employee:read, clock:write, device:write.")

    def handle(self, *args, **options):
        matches = list(Company.objects.filter(name__icontains=options["company"]))
        if len(matches) != 1:
            names = ", ".join(c.name for c in matches) or "none"
            raise CommandError(f"Need exactly one company matching '{options['company']}' (found: {names}).")
        company = matches[0]
        api_key, raw_key = ApiKey.issue(company, options["name"], options["scope"] or DEFAULT_SCOPES)
        audit("api_key.issue", actor="system:issue_api_key", company=company, target=str(api_key))
        self.stdout.write(self.style.SUCCESS(f"API key for {options['name']} at {company}:"))
        self.stdout.write(raw_key)
        self.stdout.write(self.style.WARNING("Copy it now; it cannot be shown again. Send it to the partner securely."))
