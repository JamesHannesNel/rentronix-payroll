"""Turn a pay period's clockings into paid hours, LOA and deductions per person.

Columns match the client's 'Hrs PaySpace' sheet:
    NT    normal time hours
    T1/2  hours paid at 1.5x (weekday overtime, and Saturdays under the default rule)
    DT    hours paid at 2x (Sundays and public holidays worked)
    LOA   living out allowance in rand (nights x daily rate)

Rules per day, after any unpaid break is taken off:
    public holiday worked       all hours DT
    public holiday not worked   PayRules.public_holiday_paid_hours as NT (weekdays only)
    Sunday                      all hours DT
    Saturday                    all hours T1/2 (or treated like a weekday, per PayRules)
    weekday                     up to normal_hours_per_day as NT; the rest is T1/2 once it
                                reaches overtime_after_minutes, counted down to whole steps
A DayAdjustment replaces the calculated hours for that person and day.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from django.db.models import Q
from django.utils import timezone

from apps.employees.models import Employee
from apps.timeclock.attendance import DayLine, Status, build_line, day_bounds
from apps.timeclock.models import ClockRecord

from .models import DayAdjustment, LivingOutDays, PayPeriod, PayRules, PublicHoliday

ZERO = Decimal("0.00")
WEEKDAY, SATURDAY, SUNDAY, HOLIDAY = "weekday", "saturday", "sunday", "holiday"


def _hours(delta: dt.timedelta) -> Decimal:
    return Decimal(delta.total_seconds()) / Decimal(3600)


def _step(rules: PayRules) -> Decimal:
    return Decimal(rules.rounding_minutes) / Decimal(60)


def round_nearest(hours: Decimal, step: Decimal) -> Decimal:
    return ((hours / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step).quantize(Decimal("0.01"))


def round_down(hours: Decimal, step: Decimal) -> Decimal:
    return ((hours / step).quantize(Decimal("1"), rounding=ROUND_FLOOR) * step).quantize(Decimal("0.01"))


def split_day(rules: PayRules, day: dt.date, worked: dt.timedelta, holiday: bool) -> tuple[Decimal, Decimal, Decimal]:
    """(normal, 1.5x, 2x) hours for one person's day."""
    step = _step(rules)
    w = _hours(worked)
    if w > 0 and rules.unpaid_break_minutes:
        w = max(w - Decimal(rules.unpaid_break_minutes) / 60, Decimal(0))
    weekday = day.weekday()

    if holiday:
        if w > 0:
            return ZERO, ZERO, round_nearest(w, step)
        if weekday < 5:
            return rules.public_holiday_paid_hours, ZERO, ZERO
        return ZERO, ZERO, ZERO
    if w <= 0:
        return ZERO, ZERO, ZERO
    if weekday == 6:
        return ZERO, ZERO, round_nearest(w, step)
    if weekday == 5 and rules.saturday == PayRules.SaturdayMode.ALL_OVERTIME:
        return ZERO, round_nearest(w, step), ZERO

    limit = rules.normal_hours_per_day
    normal = min(round_nearest(w, step), limit)
    excess = w - limit
    overtime = ZERO
    if excess * 60 >= rules.overtime_after_minutes and excess > 0:
        overtime = round_down(excess, step)
    return normal, overtime, ZERO


@dataclass
class DayHours:
    date: dt.date
    kind: str
    holiday_name: str
    line: DayLine
    normal: Decimal = ZERO
    overtime: Decimal = ZERO
    double: Decimal = ZERO
    adjustment: DayAdjustment | None = None
    living_out: bool = False

    @property
    def issue(self) -> bool:
        return self.adjustment is None and self.line.status in Status.NEEDS_ATTENTION

    @property
    def total(self) -> Decimal:
        return self.normal + self.overtime + self.double


@dataclass
class LoanDue:
    loan: object
    due: Decimal
    skipped: bool
    note: str
    balance_before: Decimal


@dataclass
class SavingDue:
    saving: object
    due: Decimal
    skipped: bool


@dataclass
class EmployeeSheet:
    employee: Employee
    days: list[DayHours] = field(default_factory=list)
    loa_nights: int = 0
    loa_set_by_hand: bool = False
    loa_amount: Decimal = ZERO
    loans: list[LoanDue] = field(default_factory=list)
    savings: list[SavingDue] = field(default_factory=list)

    @property
    def normal(self) -> Decimal:
        return sum((d.normal for d in self.days), ZERO)

    @property
    def overtime(self) -> Decimal:
        return sum((d.overtime for d in self.days), ZERO)

    @property
    def double(self) -> Decimal:
        return sum((d.double for d in self.days), ZERO)

    @property
    def issues(self) -> int:
        return sum(d.issue for d in self.days)

    @property
    def loan_total(self) -> Decimal:
        return sum((loan.due for loan in self.loans), ZERO)

    @property
    def saving_total(self) -> Decimal:
        return sum((s.due for s in self.savings), ZERO)

    @property
    def days_worked(self) -> int:
        return sum(1 for d in self.days if d.line.present)


def holidays_between(start: dt.date, end: dt.date) -> dict[dt.date, str]:
    return dict(PublicHoliday.objects.filter(date__range=(start, end)).values_list("date", "name"))


def _employees(period: PayPeriod, with_clocks: set[int], employee_ids=None):
    qs = Employee.objects.filter(company=period.company).select_related("site")
    if employee_ids is not None:
        qs = qs.filter(pk__in=employee_ids)
    qs = qs.filter(Q(status=Employee.Status.ACTIVE) | Q(pk__in=with_clocks))
    qs = qs.exclude(date_engaged__gt=period.end).exclude(termination_date__lt=period.start)
    return list(qs.order_by("last_name", "first_name"))


def period_sheets(period: PayPeriod, employee_ids=None, now: dt.datetime | None = None) -> list[EmployeeSheet]:
    from apps.loans.models import Loan, LoanRepayment, Saving, SavingEntry

    now = now or timezone.now()
    rules = PayRules.for_company(period.company)
    holidays = holidays_between(period.start, period.end)
    start, _ = day_bounds(period.start)
    _, end = day_bounds(period.end)

    clocks: dict[tuple[int, dt.date], list[ClockRecord]] = {}
    for clock in (ClockRecord.objects.filter(company=period.company, employee__isnull=False, clocked_at__gte=start,
                                             clocked_at__lt=end).order_by("clocked_at", "id")):
        clocks.setdefault((clock.employee_id, timezone.localtime(clock.clocked_at).date()), []).append(clock)
    with_clocks = {key[0] for key in clocks}

    employees = _employees(period, with_clocks, employee_ids)
    ids = [e.pk for e in employees]
    adjustments = {(a.employee_id, a.date): a for a in DayAdjustment.objects.filter(
        employee_id__in=ids, date__range=(period.start, period.end))}
    loa_overrides = dict(LivingOutDays.objects.filter(period=period, employee_id__in=ids)
                         .values_list("employee_id", "nights"))

    loans_by_employee: dict[int, list] = {}
    for loan in (Loan.objects.filter(employee_id__in=ids, status=Loan.Status.ACTIVE, start_date__lte=period.end)
                 .prefetch_related("repayments__period")):
        loans_by_employee.setdefault(loan.employee_id, []).append(loan)
    repayments = {r.loan_id: r for r in LoanRepayment.objects.filter(period=period)}
    savings_by_employee: dict[int, list] = {}
    for saving in Saving.objects.filter(employee_id__in=ids, status=Saving.Status.ACTIVE, start_date__lte=period.end):
        savings_by_employee.setdefault(saving.employee_id, []).append(saving)
    saving_entries = {e.saving_id: e for e in SavingEntry.objects.filter(
        period=period, kind=SavingEntry.Kind.CONTRIBUTION)}

    sheets = []
    for employee in employees:
        sheet = EmployeeSheet(employee=employee)
        for day in period.days():
            line = build_line(employee, clocks.get((employee.pk, day), []), day, now)
            holiday_name = holidays.get(day, "")
            kind = HOLIDAY if holiday_name else {5: SATURDAY, 6: SUNDAY}.get(day.weekday(), WEEKDAY)
            dh = DayHours(date=day, kind=kind, holiday_name=holiday_name, line=line)
            outside = (employee.date_engaged and day < employee.date_engaged) or (
                employee.termination_date and day > employee.termination_date)
            adjustment = adjustments.get((employee.pk, day))
            if adjustment:
                dh.adjustment = adjustment
                dh.normal, dh.overtime, dh.double = adjustment.normal, adjustment.overtime, adjustment.double
            elif not outside:
                dh.normal, dh.overtime, dh.double = split_day(rules, day, line.worked, bool(holiday_name))
            dh.living_out = bool(employee.site and employee.site.living_out and (line.present or adjustment))
            sheet.days.append(dh)

        if employee.pk in loa_overrides:
            sheet.loa_nights, sheet.loa_set_by_hand = loa_overrides[employee.pk], True
        else:
            sheet.loa_nights = sum(d.living_out for d in sheet.days)
        sheet.loa_amount = (rules.living_out_daily_rate * sheet.loa_nights).quantize(Decimal("0.01"))

        for loan in loans_by_employee.get(employee.pk, []):
            repaid_elsewhere = sum((r.amount for r in loan.repayments.all()
                                    if not r.skipped and r.period_id != period.pk
                                    and r.period.status == PayPeriod.Status.APPROVED), ZERO)
            balance = max(loan.amount - repaid_elsewhere, ZERO)
            if balance <= 0:
                continue
            rep = repayments.get(loan.pk)
            if rep and rep.skipped:
                due, skipped = ZERO, True
            elif rep and rep.amount > 0:
                due, skipped = min(rep.amount, balance), False
            else:
                due, skipped = min(loan.instalment, balance), False
            sheet.loans.append(LoanDue(loan=loan, due=due, skipped=skipped, note=rep.note if rep else "",
                                       balance_before=balance))
        for saving in savings_by_employee.get(employee.pk, []):
            entry = saving_entries.get(saving.pk)
            skipped = bool(entry and entry.skipped)
            sheet.savings.append(SavingDue(saving=saving, due=ZERO if skipped else saving.amount_per_run,
                                           skipped=skipped))
        sheets.append(sheet)
    return sheets


def suggest_next_period(company, today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """The 14 days after the last period, or the fortnight (Friday to Thursday) containing today."""
    today = today or timezone.localdate()
    last = PayPeriod.objects.filter(company=company).order_by("-end").first()
    if last:
        start = last.end + dt.timedelta(days=1)
        return start, start + (last.end - last.start)
    start = today - dt.timedelta(days=(today.weekday() - 4) % 7)
    return start, start + dt.timedelta(days=13)
