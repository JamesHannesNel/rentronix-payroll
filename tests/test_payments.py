import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.urls import reverse

from apps.core.models import AuditLog
from apps.employees.models import Employee
from apps.payments import fnb, services
from apps.payments.models import CompanyBankAccount, EmployeeBankAccount, PaymentBatch, PaymentLine

# FNB's published template, header part (rhelp_0_15/OBE_SA_Downloads/Downloads/Payments/Payment_CSV_Template.csv)
FNB_HEADINGS = (
    "RECIPIENT NAME,RECIPIENT ACCOUNT,RECIPIENT ACCOUNT TYPE,BRANCHCODE,AMOUNT,OWN REFERENCE,RECIPIENT REFERENCE,"
    "EMAIL 1 NOTIFY,EMAIL 1 ADDRESS,EMAIL 1 SUBJECT,EMAIL 2 NOTIFY,EMAIL 2 ADDRESS,EMAIL 2 SUBJECT,"
    "EMAIL 3 NOTIFY,EMAIL 3 ADDRESS,EMAIL 3 SUBJECT,EMAIL 4 NOTIFY,EMAIL 4 ADDRESS,EMAIL 4 SUBJECT,"
    "EMAIL 5 NOTIFY,EMAIL 5 ADDRESS,EMAIL 5 SUBJECT,FAX 1 NOTIFY,FAX 1 CODE,FAX 1 NUMBER,FAX 1 SUBJECT,"
    "FAX 2 NOTIFY,FAX 2 CODE,FAX 2 NUMBER,FAX 2 SUBJECT,SMS 1 NOTIFY,SMS 1 CODE,SMS 1 NUMBER,"
    "SMS 2 NOTIFY,SMS 2 CODE,SMS 2 NUMBER"
)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_superuser("andre", "andre@example.com", "a-long-test-password")


@pytest.fixture
def staff(company):
    people = [("EMP0001", "Sipho", "Dlamini", "capitec", "1234567890"),
              ("EMP0002", "Thandeka", "Mokoena", "fnb", "62000000001"),
              ("EMP0003", "Johan", "van der Merwe", "standard_bank", "0012345678")]
    result = []
    for code, first, last, bank, number in people:
        employee = Employee.objects.create(company=company, employee_code=code, first_name=first, last_name=last)
        EmployeeBankAccount.objects.create(employee=employee, bank=bank, account_number=number)
        result.append(employee)
    Employee.objects.create(company=company, employee_code="EMP0009", first_name="No", last_name="Bank")
    return result


@pytest.fixture
def batch(company, staff, user):
    paying = CompanyBankAccount.objects.create(company=company, account_number="62000031451")
    return PaymentBatch.objects.create(company=company, paying_account=paying, name="Fortnight 1-15 Nov",
                                       action_date=dt.date(2026, 11, 14), created_by=user)


def test_bank_account_numbers_are_encrypted_at_rest(staff):
    with connection.cursor() as cursor:
        cursor.execute("SELECT account_number FROM payments_employeebankaccount")
        stored = [row[0] for row in cursor.fetchall()]
    assert all(value.startswith("enc1:") for value in stored)
    assert not any("1234567890" in value for value in stored)
    assert EmployeeBankAccount.objects.get(employee=staff[0]).account_number == "1234567890"


def test_universal_branch_code_filled_in(staff):
    assert staff[0].bank_account.branch_code == "470010"  # Capitec
    assert staff[2].bank_account.branch_code == "051001"  # Standard Bank


def test_add_active_employees_reports_missing_bank_details(batch):
    added, missing = services.add_active_employees(batch, "test")
    assert added == 3 and missing == ["EMP0009"]
    assert batch.lines.get(employee__employee_code="EMP0002").recipient_name == "Thandeka Mokoena"


def test_import_amounts_accepts_local_formats(batch):
    text = "Employee Code,Net Pay\nEMP0001,\"R 4 512,30\"\nemp0002,10250.00\nEMP0003,980\n"
    result = services.import_amounts(batch, text, "test")
    assert result.errors == [] and result.added == 3
    amounts = dict(batch.lines.values_list("employee__employee_code", "amount"))
    assert amounts == {"EMP0001": Decimal("4512.30"), "EMP0002": Decimal("10250.00"), "EMP0003": Decimal("980.00")}


def test_import_is_all_or_nothing(batch):
    result = services.import_amounts(batch, "EMP0001,100\nEMP0404,50\nEMP0009,70\nEMP0002,abc\n", "test")
    assert len(result.errors) == 3
    assert not batch.lines.exists()


def test_approve_blocks_problems_then_locks(batch, user):
    services.add_active_employees(batch, "test")
    problems = services.approve(batch, user)
    assert {p.employee_code for p in problems} == {"EMP0001", "EMP0002", "EMP0003"}  # all still R0.00
    services.import_amounts(batch, "EMP0001,100\nEMP0002,200\n", "test")
    services.remove_zero_lines(batch, "test")
    assert services.approve(batch, user) == []
    batch.refresh_from_db()
    assert batch.status == PaymentBatch.Status.APPROVED and batch.approved_by == user
    with pytest.raises(services.BatchError):
        services.import_amounts(batch, "EMP0001,999\n", "test")


def test_duplicate_bank_account_is_flagged(batch, staff):
    services.import_amounts(batch, "EMP0001,100\nEMP0002,200\n", "test")
    PaymentLine.objects.filter(batch=batch, employee=staff[1]).update(branch_code="470010")
    line = batch.lines.get(employee=staff[1])
    line.account_number = "1234567890"
    line.save()
    assert any("Same bank account" in p.message for p in fnb.check(batch))


def test_fnb_file_matches_fnb_template(batch, user, company):
    company.trading_name = "Demo Construction"
    company.save()
    services.import_amounts(batch, "EMP0002,10250\nEMP0001,4512.3\n", "test")
    services.approve(batch, user)
    name, text = services.export(batch, user)

    assert name == "FNB-20261114-Fortnight-1-15-Nov.csv"
    lines = text.split("\r\n")
    assert lines[-1] == "" and all("\n" not in line for line in lines)
    assert lines[0] == "BInSol - U ver 1.00" + "," * 35
    assert lines[1] == "14-11-2026" + "," * 35
    assert lines[2] == "62000031451," + "," * 34
    assert lines[3] == FNB_HEADINGS
    assert all(line.count(",") == 35 for line in lines[:-1])
    assert lines[4].startswith("Sipho Dlamini,1234567890,2,470010,4512.30,SALARIES,Demo Construction,")
    assert lines[5].startswith("Thandeka Mokoena,62000000001,2,250655,10250.00,SALARIES,Demo Construction,")
    assert len(lines) == 7

    batch.refresh_from_db()
    assert batch.status == PaymentBatch.Status.EXPORTED
    assert AuditLog.objects.filter(action="payments.export_fnb").exists()


def test_text_is_made_bank_safe():
    assert fnb.clean_text('Van der Merwe, "Jo"', 60) == "Van der Merwe Jo"
    assert fnb.clean_text("A very long own reference text", 15) == "A very long own"


def test_draft_batch_cannot_be_exported(batch, user):
    with pytest.raises(services.BatchError):
        services.export(batch, user)


def test_admin_flow(client, batch, user):
    client.force_login(user)
    url = reverse("admin:payments_paymentbatch_change", args=[batch.pk])
    assert client.get(url).status_code == 200
    client.post(reverse("admin:payments_batch_add_employees", args=[batch.pk]))
    upload = SimpleUploadedFile("net.csv", b"code,amount\nEMP0001,100\nEMP0002,200\nEMP0003,300\n")
    response = client.post(reverse("admin:payments_batch_import_amounts", args=[batch.pk]), {"file": upload})
    assert response.status_code == 302
    client.post(reverse("admin:payments_batch_approve", args=[batch.pk]))
    page = client.get(url).content.decode()
    assert "Download FNB file" in page and "Ready for FNB" in page
    download = client.post(reverse("admin:payments_batch_fnb_file", args=[batch.pk]))
    assert download["Content-Disposition"].startswith('attachment; filename="FNB-20261114')
    assert download.content.decode().startswith("BInSol - U ver 1.00")
    employee_page = client.get(reverse("admin:employees_employee_change", args=[batch.lines.first().employee_id]))
    assert employee_page.status_code == 200 and "Bank details" in employee_page.content.decode()
