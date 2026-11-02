from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import Company
from apps.employees.models import Employee

HOURS = {"max_digits": 6, "decimal_places": 2, "validators": [MinValueValidator(Decimal("0"))]}


class PayRules(models.Model):
    """How clocked time becomes paid hours, per company. Defaults match the
    first client's spreadsheet; every value is editable in Settings."""

    class SaturdayMode(models.TextChoices):
        ALL_OVERTIME = "ot15", "Every Saturday hour at 1.5×"
        LIKE_WEEKDAY = "weekday", "Like a weekday (normal hours, then 1.5×)"

    company = models.OneToOneField(Company, on_delete=models.CASCADE, related_name="pay_rules")
    normal_hours_per_day = models.DecimalField(default=Decimal("9.00"), max_digits=4, decimal_places=2,
                                               validators=[MinValueValidator(Decimal("1")), MaxValueValidator(Decimal("12"))],
                                               help_text="Hours paid at the normal rate on a weekday.")
    overtime_after_minutes = models.PositiveSmallIntegerField(
        default=30, help_text="Overtime only counts once someone works this many minutes past the normal day.")
    rounding_minutes = models.PositiveSmallIntegerField(
        default=30, validators=[MinValueValidator(1), MaxValueValidator(60)],
        help_text="Overtime is counted in steps of this many minutes (rounded down). Normal hours round to the nearest step.")
    unpaid_break_minutes = models.PositiveSmallIntegerField(
        default=0, help_text="Taken off each worked day before hours are split, e.g. 30 for an unpaid lunch.")
    saturday = models.CharField(max_length=10, choices=SaturdayMode.choices, default=SaturdayMode.ALL_OVERTIME)
    public_holiday_paid_hours = models.DecimalField(
        default=Decimal("9.00"), max_digits=4, decimal_places=2,
        help_text="Normal hours given to everyone on a weekday public holiday they don't work.")
    living_out_daily_rate = models.DecimalField(default=Decimal("65.00"), max_digits=8, decimal_places=2,
                                                help_text="Living out allowance (LOA) per night, in rand.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = verbose_name_plural = "pay rules"

    def __str__(self):
        return f"Pay rules for {self.company}"

    @classmethod
    def for_company(cls, company):
        rules, _ = cls.objects.get_or_create(company=company)
        return rules


class PublicHoliday(models.Model):
    date = models.DateField(unique=True)
    name = models.CharField(max_length=80)

    class Meta:
        ordering = ["date"]

    def __str__(self):
        return f"{self.date:%d %b %Y} {self.name}"


class PayPeriod(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        APPROVED = "approved", "Approved"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="pay_periods")
    name = models.CharField(max_length=60)
    start = models.DateField()
    end = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.start and self.end and self.end < self.start:
            raise ValidationError("The period ends before it starts.")
        if self.start and self.end and (self.end - self.start).days > 31:
            raise ValidationError("A pay period can be at most 31 days.")

    @property
    def is_locked(self):
        return self.status == self.Status.APPROVED

    def days(self):
        from datetime import timedelta

        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]


class DayAdjustment(models.Model):
    """A person's corrected hours for one day, replacing what the clocks give."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="day_adjustments")
    date = models.DateField()
    normal = models.DecimalField(**HOURS)
    overtime = models.DecimalField("1.5× hours", **HOURS)
    double = models.DecimalField("2× hours", **HOURS)
    note = models.CharField(max_length=200)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["employee", "date"], name="uniq_day_adjustment")]

    def __str__(self):
        return f"{self.employee.employee_code} {self.date}"


class LivingOutDays(models.Model):
    """LOA nights for one person in one period, when set by hand."""

    period = models.ForeignKey(PayPeriod, on_delete=models.CASCADE, related_name="living_out")
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="+")
    nights = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["period", "employee"], name="uniq_loa_days")]
