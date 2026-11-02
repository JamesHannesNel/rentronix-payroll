"""Load employee bank details from a CSV, e.g. when setting up a new client.

    docker compose exec -T web python manage.py import_bank_details --company "Demo" < bank.csv

Columns (heading row required): employee_code, bank, account_number, account_type, branch_code
  bank:          absa, african_bank, bidvest, capitec, discovery, fnb, investec, nedbank,
                 standard_bank, tymebank or other
  account_type:  savings, current or transmission
  branch_code:   optional; blank uses the bank's universal branch code (required for 'other')

Nothing is saved if any row is wrong. The file contains personal information:
delete it once imported.
"""

import csv
import sys

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Company, audit
from apps.employees.models import Employee
from apps.payments.models import UNIVERSAL_BRANCH_CODES, AccountType, EmployeeBankAccount

TYPES = {"savings": AccountType.SAVINGS, "current": AccountType.CURRENT, "cheque": AccountType.CURRENT,
         "transmission": AccountType.TRANSMISSION, "1": AccountType.CURRENT, "2": AccountType.SAVINGS,
         "3": AccountType.TRANSMISSION}


class Command(BaseCommand):
    help = "Import employee bank details from CSV on standard input."

    def add_arguments(self, parser):
        parser.add_argument("--company", required=True)

    def handle(self, *args, **options):
        matches = list(Company.objects.filter(name__icontains=options["company"]))
        if len(matches) != 1:
            raise CommandError(f"Need exactly one company matching '{options['company']}'.")
        company = matches[0]
        employees = {e.employee_code.upper(): e for e in Employee.objects.filter(company=company)}
        reader = csv.DictReader(sys.stdin)
        records, errors = [], []
        for number, row in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            employee = employees.get(row.get("employee_code", "").upper())
            bank = row.get("bank", "").lower().replace(" ", "_")
            account_type = TYPES.get(row.get("account_type", "").lower())
            if employee is None:
                errors.append(f"Row {number}: unknown employee code {row.get('employee_code')!r}")
                continue
            if bank not in UNIVERSAL_BRANCH_CODES and bank != "other":
                errors.append(f"Row {number}: unknown bank {row.get('bank')!r}")
                continue
            if account_type is None:
                errors.append(f"Row {number}: account_type must be savings, current or transmission")
                continue
            record = EmployeeBankAccount(employee=employee, bank=bank, branch_code=row.get("branch_code", ""),
                                         account_number=row.get("account_number", "").replace(" ", ""),
                                         account_type=account_type)
            if not record.branch_code and bank in UNIVERSAL_BRANCH_CODES:
                record.branch_code = UNIVERSAL_BRANCH_CODES[bank][1]
            try:
                record.full_clean(exclude=["employee"])
            except ValidationError as exc:
                errors.append(f"Row {number}: {'; '.join(m for ms in exc.message_dict.values() for m in ms)}")
                continue
            records.append(record)
        if errors:
            raise CommandError("Nothing imported:\n" + "\n".join(errors))
        with transaction.atomic():
            for record in records:
                EmployeeBankAccount.objects.update_or_create(
                    employee=record.employee,
                    defaults={"bank": record.bank, "branch_code": record.branch_code,
                              "account_number": record.account_number, "account_type": record.account_type})
            audit("payments.import_bank_details", actor="system:import_bank_details", company=company,
                  target="bank details", count=len(records))
        self.stdout.write(self.style.SUCCESS(f"Saved bank details for {len(records)} employees."))
