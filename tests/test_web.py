import datetime as dt

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Company, Membership
from apps.employees.models import Employee, Site
from apps.payments.models import CompanyBankAccount, EmployeeBankAccount, PaymentBatch
from apps.timeclock.attendance import Status, attendance_for_day
from apps.timeclock.models import ClockRecord

SAST = dt.timezone(dt.timedelta(hours=2))
DAY = dt.date(2026, 11, 2)


def at(hour, minute=0, day=DAY):
    return dt.datetime.combine(day, dt.time(hour, minute), SAST)


@pytest.fixture
def site(company):
    return Site.objects.create(company=company, name="Umhlanga site")


@pytest.fixture
def worker(company, site):
    e = Employee.objects.create(company=company, employee_code="E1", first_name="Sipho", last_name="Dlamini", site=site)
    EmployeeBankAccount.objects.create(employee=e, bank="capitec", account_number="1234567890")
    return e


def add_clock(company, employee, when, direction, rid):
    return ClockRecord.objects.create(company=company, employee=employee, employee_code=employee.employee_code,
                                      reader_serial="R1", record_id=rid, clocked_at=when, direction=direction)


@pytest.fixture
def owner(company):
    user = get_user_model().objects.create_user("andre", password="a-long-test-password")
    Membership.objects.create(user=user, company=company, role=Membership.Role.OWNER)
    return user


@pytest.fixture
def supervisor(company):
    user = get_user_model().objects.create_user("team", password="a-long-test-password")
    Membership.objects.create(user=user, company=company, role=Membership.Role.SUPERVISOR)
    return user


# ---- attendance rules ----

def test_full_day(company, worker):
    add_clock(company, worker, at(7), "in", "1")
    add_clock(company, worker, at(16, 30), "out", "2")
    line = attendance_for_day(company, DAY, now=at(20))[0]
    assert line.status == Status.DONE and str(line.hours) == "9.50"


def test_lunch_break_split(company, worker):
    for rid, (h, m, d) in enumerate([(7, 0, "in"), (12, 0, "out"), (12, 30, "in"), (16, 0, "out")]):
        add_clock(company, worker, at(h, m), d, str(rid))
    assert str(attendance_for_day(company, DAY, now=at(20))[0].hours) == "8.50"


def test_unknown_direction_alternates(company, worker):
    add_clock(company, worker, at(7), "unknown", "1")
    add_clock(company, worker, at(15), "unknown", "2")
    line = attendance_for_day(company, DAY, now=at(20))[0]
    assert line.status == Status.DONE and str(line.hours) == "8.00"


def test_still_on_site_today_vs_missing_out_later(company, worker):
    add_clock(company, worker, at(7), "in", "1")
    assert attendance_for_day(company, DAY, now=at(10))[0].status == Status.ON_SITE
    assert attendance_for_day(company, DAY, now=at(10, day=DAY + dt.timedelta(days=1)))[0].status == Status.MISSING_OUT


def test_out_without_in(company, worker):
    add_clock(company, worker, at(16), "out", "1")
    assert attendance_for_day(company, DAY, now=at(20))[0].status == Status.MISSING_IN


def test_absent_and_site_filter(company, worker, site):
    other = Site.objects.create(company=company, name="Ballito site")
    Employee.objects.create(company=company, employee_code="E2", first_name="A", last_name="B", site=other)
    lines = attendance_for_day(company, DAY, now=at(20))
    assert [line.status for line in lines] == [Status.ABSENT, Status.ABSENT]
    assert len(attendance_for_day(company, DAY, site=site, now=at(20))) == 1


# ---- screens ----

def test_home_requires_sign_in(client, db):
    response = client.get("/")
    assert response.status_code == 302 and response["Location"].startswith("/login/")
    assert client.get("/login/").status_code == 200


def test_screens_render(client, owner, worker, company):
    add_clock(company, worker, timezone.now() - dt.timedelta(hours=1), "in", "1")
    client.force_login(owner)
    for url in ["/", "/attendance/", "/attendance/?show=attention", "/attendance/unknown/", "/employees/",
                f"/employees/{worker.pk}/", "/pay/", "/pay/new/", "/readers/", "/employees/?missing=bank"]:
        response = client.get(url)
        assert response.status_code == 200, url
    assert "Site board" in client.get("/").content.decode()


def test_users_only_see_their_companies(client, owner, other_company):
    stranger = Employee.objects.create(company=other_company, employee_code="X", first_name="Not", last_name="Mine")
    client.force_login(owner)
    assert client.get(f"/employees/{stranger.pk}/").status_code == 404
    client.post("/company/", {"company": other_company.pk})
    assert client.get(f"/employees/{stranger.pk}/").status_code == 404


def test_user_without_company(client, db):
    user = get_user_model().objects.create_user("nobody", password="a-long-test-password")
    client.force_login(user)
    assert client.get("/").status_code == 403


def test_supervisor_cannot_open_pay_runs(client, supervisor):
    client.force_login(supervisor)
    assert client.get("/pay/").status_code == 403
    assert "Pay runs</a>" not in client.get("/").content.decode()


def test_pay_run_end_to_end(client, owner, worker, company):
    CompanyBankAccount.objects.create(company=company, account_number="62000031451")
    client.force_login(owner)
    response = client.post("/pay/new/", {"name": "Fortnight 1-15 Nov", "action_date": "2026-11-14",
                                         "paying_account": CompanyBankAccount.objects.get().pk})
    batch = PaymentBatch.objects.get()
    assert response["Location"] == reverse("web:pay_run", args=[batch.pk])
    line = batch.lines.get()

    client.post(f"/pay/{batch.pk}/amounts/", {f"amount_{line.pk}": "4 512,30"})
    line.refresh_from_db()
    assert str(line.amount) == "4512.30"

    client.post(f"/pay/{batch.pk}/import/", {"file": SimpleUploadedFile("n.csv", b"code,net\nE1,5000\n")})
    line.refresh_from_db()
    assert str(line.amount) == "5000.00"

    client.post(f"/pay/{batch.pk}/approve/")
    batch.refresh_from_db()
    assert batch.status == PaymentBatch.Status.APPROVED
    download = client.post(f"/pay/{batch.pk}/download/")
    assert download.content.decode().startswith("BInSol - U ver 1.00")
    assert "Ready for FNB" in client.get(f"/pay/{batch.pk}/").content.decode() or batch.status


def test_demo_clocks_only_for_mock_company(db, company):
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("demo_clocks")
    mock = Company.objects.create(name="Demo (MOCK)")
    Employee.objects.create(company=mock, employee_code="M1", first_name="A", last_name="B")
    call_command("demo_clocks", days=3)
    assert ClockRecord.objects.filter(company=mock).exists()
    assert not ClockRecord.objects.filter(company=company).exists()
