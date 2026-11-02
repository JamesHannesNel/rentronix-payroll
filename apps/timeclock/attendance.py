"""Turn raw clock records into one attendance line per employee per day.

Pairing: clocks are taken in time order. A clock marked "in" or "out" is
used as marked; an "unknown" clock is treated as the opposite of the one
before it (first of the day = in). Each in followed by an out is a worked
interval.

Not yet handled: shifts that cross midnight (Phase 1 clock templates).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from django.utils import timezone

from apps.employees.models import Employee

from .models import ClockRecord


class Status:
    ABSENT = "absent"
    ON_SITE = "on_site"
    DONE = "done"
    MISSING_OUT = "missing_out"
    MISSING_IN = "missing_in"

    LABELS = {
        ABSENT: "Absent",
        ON_SITE: "On site",
        DONE: "Clocked out",
        MISSING_OUT: "No clock-out",
        MISSING_IN: "No clock-in",
    }
    NEEDS_ATTENTION = {MISSING_OUT, MISSING_IN}


@dataclass
class DayLine:
    employee: Employee
    clocks: list[ClockRecord] = field(default_factory=list)
    first_in: dt.datetime | None = None
    last_out: dt.datetime | None = None
    worked: dt.timedelta = dt.timedelta()
    status: str = Status.ABSENT

    @property
    def status_label(self) -> str:
        return Status.LABELS[self.status]

    @property
    def needs_attention(self) -> bool:
        return self.status in Status.NEEDS_ATTENTION

    @property
    def hours(self) -> Decimal:
        return (Decimal(self.worked.total_seconds()) / 3600).quantize(Decimal("0.01"))

    @property
    def present(self) -> bool:
        return self.status != Status.ABSENT


def day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(dt.datetime.combine(day, dt.time.min), tz)
    return start, start + dt.timedelta(days=1)


def build_line(employee: Employee, clocks: list[ClockRecord], day: dt.date, now: dt.datetime) -> DayLine:
    line = DayLine(employee=employee, clocks=clocks)
    if not clocks:
        return line
    open_in: dt.datetime | None = None
    previous = ClockRecord.Direction.OUT
    orphan_out = False
    for clock in clocks:
        direction = clock.direction
        if direction == ClockRecord.Direction.UNKNOWN:
            direction = ClockRecord.Direction.IN if previous == ClockRecord.Direction.OUT else ClockRecord.Direction.OUT
        if direction == ClockRecord.Direction.IN:
            if open_in is None:
                open_in = clock.clocked_at
            line.first_in = line.first_in or clock.clocked_at
        else:
            if open_in is not None:
                line.worked += clock.clocked_at - open_in
                open_in = None
            else:
                orphan_out = True
            line.last_out = clock.clocked_at
        previous = direction

    if open_in is not None:
        is_today = day == timezone.localdate(now)
        line.status = Status.ON_SITE if is_today else Status.MISSING_OUT
    elif orphan_out and line.first_in is None:
        line.status = Status.MISSING_IN
    else:
        line.status = Status.DONE
    return line


def attendance_for_day(company, day: dt.date, site=None, now: dt.datetime | None = None) -> list[DayLine]:
    """One line per active employee (plus anyone terminated who still clocked that day)."""
    now = now or timezone.now()
    start, end = day_bounds(day)
    clocks = (ClockRecord.objects.filter(company=company, employee__isnull=False, clocked_at__gte=start,
                                         clocked_at__lt=end).order_by("clocked_at", "id"))
    by_employee: dict[int, list[ClockRecord]] = {}
    for clock in clocks:
        by_employee.setdefault(clock.employee_id, []).append(clock)

    employees = Employee.objects.filter(company=company).select_related("site")
    if site is not None:
        employees = employees.filter(site=site)
    lines = []
    for employee in employees.order_by("site__name", "last_name", "first_name"):
        if employee.status != Employee.Status.ACTIVE and employee.id not in by_employee:
            continue
        lines.append(build_line(employee, by_employee.get(employee.id, []), day, now))
    return lines


def unmatched_count(company, day: dt.date) -> int:
    start, end = day_bounds(day)
    return ClockRecord.objects.filter(company=company, employee__isnull=True, clocked_at__gte=start,
                                      clocked_at__lt=end).count()
