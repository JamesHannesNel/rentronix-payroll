"""South African public holidays for 2026, including observed Mondays and the
4 November 2026 election day declared by the President. Add later years in
Setup and settings -> Public holidays."""

import datetime as dt

from django.db import migrations

HOLIDAYS_2026 = [
    ("2026-01-01", "New Year's Day"),
    ("2026-03-21", "Human Rights Day"),
    ("2026-04-03", "Good Friday"),
    ("2026-04-06", "Family Day"),
    ("2026-04-27", "Freedom Day"),
    ("2026-05-01", "Workers' Day"),
    ("2026-06-16", "Youth Day"),
    ("2026-08-10", "National Women's Day (observed)"),
    ("2026-09-24", "Heritage Day"),
    ("2026-11-04", "Local government elections"),
    ("2026-12-16", "Day of Reconciliation"),
    ("2026-12-25", "Christmas Day"),
    ("2026-12-26", "Day of Goodwill"),
]


def add(apps, schema_editor):
    PublicHoliday = apps.get_model("timesheets", "PublicHoliday")
    for date, name in HOLIDAYS_2026:
        PublicHoliday.objects.get_or_create(date=dt.date.fromisoformat(date), defaults={"name": name})


def remove(apps, schema_editor):
    PublicHoliday = apps.get_model("timesheets", "PublicHoliday")
    PublicHoliday.objects.filter(date__in=[dt.date.fromisoformat(d) for d, _ in HOLIDAYS_2026]).delete()


class Migration(migrations.Migration):
    dependencies = [("timesheets", "0001_initial")]
    operations = [migrations.RunPython(add, remove)]
