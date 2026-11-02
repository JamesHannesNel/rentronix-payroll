import datetime as dt
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model

from apps.employees.models import Employee, Site
from apps.loans.models import Loan, LoanRepayment, Saving
from apps.timeclock.models import ClockRecord
from apps.timesheets import services
from apps.timesheets.engine import period_sheets, split_day, suggest_next_period
from apps.timesheets.models import PayPeriod, PayRules, PublicHoliday

SAST = dt.timezone(dt.timedelta(hours=2))
FRI = dt.date(2026, 9, 4)  # the spreadsheet's fortnight: Fri 4 Sep to Thu 17 Sep 2026
MON, SAT, SUN = dt.date(2026, 9, 7), dt.date(2026, 9, 5), dt.date(2026, 9, 6)
HERITAGE = dt.date(2026, 9, 24)


@pytest.fixture
def rules(company):
    return PayRules.for_company(company)


def h(hours, minutes=0):
    return dt.timedelta(hours=hours, minutes=minutes)


# ---- one day ----

@pytest.mark.parametrize("worked, expected", [
    (h(9), (D("9.00"), D("0.00"), D("0.00"))),
    (h(9, 20), (D("9.00"), D("0.00"), D("0.00"))),      # 20 min past: under the 30-minute threshold
    (h(9, 35), (D("9.00"), D("0.50"), D("0.00"))),      # 35 min past: half an hour
    (h(10, 50), (D("9.00"), D("1.50"), D("0.00"))),     # 1h50 past: counted down to 1.5
    (h(7, 10), (D("7.00"), D("0.00"), D("0.00"))),      # short day: nearest half hour
    (h(8, 45), (D("9.00"), D("0.00"), D("0.00"))),      # 8h45 rounds to 9 (half up)
])
def test_weekday(rules, worked, expected):
    assert split_day(rules, MON, worked, holiday=False) == expected


def test_saturday_all_time_and_a_half_by_default(rules):
    assert split_day(rules, SAT, h(8), holiday=False) == (D("0.00"), D("8.00"), D("0.00"))
    rules.saturday = PayRules.SaturdayMode.LIKE_WEEKDAY
    assert split_day(rules, SAT, h(8), holiday=False) == (D("8.00"), D("0.00"), D("0.00"))


def test_sunday_and_public_holiday(rules):
    assert split_day(rules, SUN, h(6), holiday=False) == (D("0.00"), D("0.00"), D("6.00"))
    assert split_day(rules, HERITAGE, h(8), holiday=True) == (D("0.00"), D("0.00"), D("8.00"))
    assert split_day(rules, HERITAGE, h(0), holiday=True) == (D("9.00"), D("0.00"), D("0.00"))


def test_unpaid_break(rules):
    rules.unpaid_break_minutes = 30
    assert split_day(rules, MON, h(9, 30), holiday=False) == (D("9.00"), D("0.00"), D("0.00"))


# ---- a whole period ----

@pytest.fixture
def camp(company):
    return Site.objects.create(company=company, name="Ballito camp", living_out=True)


@pytest.fixture
def worker(company, camp):
    return Employee.objects.create(company=company, employee_code="PC007", first_name="Xolani", last_name="Cele",
                                   site=camp)


@pytest.fixture
def period(company):
    return PayPeriod.objects.create(company=company, name="Fortnight 4-17 Sep", start=FRI,
                                    end=FRI + dt.timedelta(days=13))


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("andre", password="a-long-test-password")


def work(company, employee, day, start, end, seq):
    for i, (t, direction) in enumerate([(start, "in"), (end, "out")]):
        ClockRecord.objects.create(company=company, employee=employee, employee_code=employee.employee_code,
                                   reader_serial="R1", record_id=f"{seq}-{i}", direction=direction,
                                   clocked_at=dt.datetime.combine(day, t, SAST))


def test_fortnight_matches_spreadsheet_style_totals(company, worker, period):
    seq = 0
    for day in period.days():
        seq += 1
        if day.weekday() < 5:
            work(company, worker, day, dt.time(7), dt.time(16, 0), seq)   # 9h
        elif day.weekday() == 5:
            work(company, worker, day, dt.time(7), dt.time(15), seq)      # 8h Saturday
    sheet = period_sheets(period)[0]
    assert sheet.normal == D("90.00")      # 10 weekdays x 9
    assert sheet.overtime == D("16.00")    # two Saturdays x 8 at 1.5x
    assert sheet.double == D("0.00")
    assert sheet.loa_nights == 12          # days worked at a sleep-over site
    assert sheet.loa_amount == D("780.00")  # 12 x R65


def test_adjustment_and_loa_override(company, worker, period, user):
    work(company, worker, MON, dt.time(7), dt.time(16), 1)
    ClockRecord.objects.create(company=company, employee=worker, employee_code="PC007", reader_serial="R1",
                               record_id="x", direction="in", clocked_at=dt.datetime.combine(
                                   MON + dt.timedelta(days=1), dt.time(7), SAST))
    sheet = period_sheets(period, now=dt.datetime(2026, 10, 1, tzinfo=SAST))[0]
    assert sheet.issues == 1  # Tuesday has no clock-out

    services.save_adjustment(period, worker, MON + dt.timedelta(days=1), D("9"), D("1"), D("0"), "Forgot to clock out",
                             user)
    services.set_loa_nights(period, worker, 14, user)
    sheet = period_sheets(period, now=dt.datetime(2026, 10, 1, tzinfo=SAST))[0]
    assert sheet.issues == 0
    assert sheet.normal == D("18.00") and sheet.overtime == D("1.00")
    assert sheet.loa_nights == 14 and sheet.loa_set_by_hand and sheet.loa_amount == D("910.00")


def test_public_holiday_in_period(company, worker):
    period = PayPeriod.objects.create(company=company, name="Heritage week", start=HERITAGE, end=HERITAGE)
    assert PublicHoliday.objects.filter(date=HERITAGE).exists()  # loaded by migration
    sheet = period_sheets(period)[0]
    assert sheet.days[0].kind == "holiday" and sheet.normal == D("9.00")


def test_loans_deduct_skip_and_settle(company, worker, period, user):
    loan = Loan.objects.create(company=company, employee=worker, kind=Loan.Kind.LOAN, amount=D("2000"),
                               instalment=D("500"), start_date=dt.date(2026, 9, 9))
    phone = Loan.objects.create(company=company, employee=worker, kind=Loan.Kind.PHONE, amount=D("400"),
                                instalment=D("500"), start_date=dt.date(2026, 9, 1))
    Saving.objects.create(company=company, employee=worker, amount_per_run=D("100"), start_date=FRI)
    sheet = period_sheets(period)[0]
    due = {d.loan.kind: d.due for d in sheet.loans}
    assert due == {"loan": D("500.00"), "phone": D("400.00")}  # the phone's last instalment is capped at its balance
    assert sheet.loan_total == D("900.00") and sheet.saving_total == D("100")

    services.set_loan_choice(period, loan, skipped=True, amount=None, note="Only worked 5 days", user=user)
    assert period_sheets(period)[0].loan_total == D("400.00")

    services.approve(period, user)
    phone.refresh_from_db()
    loan.refresh_from_db()
    assert phone.status == Loan.Status.SETTLED and phone.balance == 0
    assert loan.status == Loan.Status.ACTIVE and loan.balance == D("2000")
    assert LoanRepayment.objects.get(loan=loan, period=period).skipped
    with pytest.raises(services.PeriodLocked):
        services.set_loa_nights(period, worker, 3, user)

    nxt = PayPeriod.objects.create(company=company, name="next", start=dt.date(2026, 9, 18), end=dt.date(2026, 10, 1))
    assert [d.due for d in period_sheets(nxt)[0].loans] == [D("500.00")]
    assert worker.savings.get().balance == D("100")


def test_reopen_reactivates_settled_loan(company, worker, period, user):
    loan = Loan.objects.create(company=company, employee=worker, amount=D("300"), instalment=D("300"),
                               start_date=FRI)
    services.approve(period, user)
    loan.refresh_from_db()
    assert loan.status == Loan.Status.SETTLED
    services.reopen(period, user)
    loan.refresh_from_db()
    assert loan.status == Loan.Status.ACTIVE


def test_exports(company, worker, period):
    work(company, worker, MON, dt.time(7), dt.time(17), 1)
    text = services.export_csv(period)
    assert text.splitlines()[0].startswith("Employee number,Name,NT,T1/2,DT,LOA nights,LOA (R)")
    assert "PC007,Xolani Cele,9.00,1.00,0.00,1,65.00" in text
    from io import BytesIO

    from openpyxl import load_workbook
    wb = load_workbook(BytesIO(services.export_xlsx(period)))
    assert wb.sheetnames == ["Hrs PaySpace", "Daily hours", "Loans and savings", "Rules used"]
    assert wb["Hrs PaySpace"]["A2"].value == "PC007" and wb["Hrs PaySpace"]["C2"].value == 9.0


def test_suggest_next_period(company):
    assert suggest_next_period(company, today=dt.date(2026, 11, 2)) == (dt.date(2026, 10, 30), dt.date(2026, 11, 12))
    PayPeriod.objects.create(company=company, name="a", start=FRI, end=FRI + dt.timedelta(days=13))
    assert suggest_next_period(company) == (dt.date(2026, 9, 18), dt.date(2026, 10, 1))
