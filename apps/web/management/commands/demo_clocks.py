"""Fill the mock company with two weeks of realistic clockings, for trying out the screens.

    sudo docker compose exec web python manage.py demo_clocks

Only works on companies whose name contains "(MOCK)", so it can never touch real data.
Running it again replaces the demo clockings.
"""

import datetime as dt
import random

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.models import Company
from apps.employees.models import Employee
from apps.timeclock.models import ClockRecord, Device


class Command(BaseCommand):
    help = "Create demo clockings for the mock company (never for a real one)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=14)

    def handle(self, *args, **options):
        companies = list(Company.objects.filter(name__contains="(MOCK)"))
        if not companies:
            raise CommandError("No mock company. Run payspace_link --payspace-company 900001 and payspace_pull first.")
        rng = random.Random(42)
        tz = timezone.get_current_timezone()
        now = timezone.localtime()
        today = now.date()

        for company in companies:
            employees = list(Employee.objects.filter(company=company, status=Employee.Status.ACTIVE).select_related("site"))
            with transaction.atomic():
                ClockRecord.objects.filter(company=company, reader_serial__startswith="DEMO-").delete()
                devices = {}
                for e in employees:
                    key = e.site_id or 0
                    if key not in devices:
                        devices[key], _ = Device.objects.update_or_create(
                            company=company, serial=f"DEMO-{key:03d}",
                            defaults={"name": f"{e.site.name if e.site else 'Office'} reader", "site": e.site,
                                      "is_online": True, "last_seen_at": timezone.now()})
                records, seq = [], 0

                def clock(e, when, direction, company=company, devices=devices, records=records):
                    nonlocal seq
                    seq += 1
                    records.append(ClockRecord(
                        company=company, employee=e, employee_code=e.employee_code, device=devices[e.site_id or 0],
                        reader_serial=devices[e.site_id or 0].serial, record_id=str(seq), clocked_at=when,
                        direction=direction, method="face"))

                last_workday = next(o for o in range(1, 8) if (today - dt.timedelta(days=o)).weekday() != 6)
                for offset in range(options["days"], -1, -1):
                    day = today - dt.timedelta(days=offset)
                    if day.weekday() == 6:
                        continue  # no work on Sundays
                    for i, e in enumerate(employees):
                        if rng.random() < 0.08 and offset:
                            continue  # absent
                        start = dt.datetime.combine(day, dt.time(6, 40), tz) + dt.timedelta(minutes=rng.randint(0, 35))
                        if day.weekday() == 5:
                            end = start + dt.timedelta(hours=5, minutes=rng.randint(0, 30))
                        else:
                            end = start + dt.timedelta(hours=9, minutes=rng.randint(0, 75))
                        if offset == 0:
                            if start <= now:
                                clock(e, start, ClockRecord.Direction.IN)
                                if end <= now:
                                    clock(e, end, ClockRecord.Direction.OUT)
                            continue
                        clock(e, start, ClockRecord.Direction.IN)
                        if offset == last_workday and i == 0:
                            continue  # forgot to clock out on the last working day: shows as "No clock-out"
                        clock(e, end, ClockRecord.Direction.OUT)

                if employees and devices:
                    any_device = next(iter(devices.values()))
                    seq += 1
                    records.append(ClockRecord(
                        company=company, employee=None, employee_code="EMP0099", device=any_device,
                        reader_serial=any_device.serial, record_id=str(seq),
                        clocked_at=dt.datetime.combine(today, dt.time(7, 5), tz), direction="in", method="face"))
                ClockRecord.objects.bulk_create(records)
            self.stdout.write(self.style.SUCCESS(f"{company}: {len(records)} demo clockings over {options['days']} days."))
