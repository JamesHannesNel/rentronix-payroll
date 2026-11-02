"""Staff loans, advances and savings, deducted a set amount each pay run."""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum

from apps.core.models import Company
from apps.employees.models import Employee
from apps.timesheets.models import PayPeriod

MONEY = {"max_digits": 10, "decimal_places": 2}
POSITIVE = [MinValueValidator(Decimal("0.01"))]


class Loan(models.Model):
    class Kind(models.TextChoices):
        LOAN = "loan", "Loan"
        ADVANCE = "advance", "Salary advance"
        PHONE = "phone", "Cellphone"
        TOOLS = "tools", "Lost or damaged tools"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        ACTIVE = "active", "Being repaid"
        SETTLED = "settled", "Paid off"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="loans")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="loans")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.LOAN)
    amount = models.DecimalField(**MONEY, validators=POSITIVE, help_text="Total to be repaid, in rand.")
    instalment = models.DecimalField(**MONEY, validators=POSITIVE, help_text="Deducted each pay run.")
    start_date = models.DateField(help_text="First pay period ending on or after this date deducts.")
    note = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["employee__last_name", "start_date"]

    def __str__(self):
        return f"{self.get_kind_display()} R{self.amount} for {self.employee}"

    @property
    def repaid(self) -> Decimal:
        total = self.repayments.filter(skipped=False, period__status=PayPeriod.Status.APPROVED).aggregate(
            s=Sum("amount"))["s"]
        return total or Decimal("0.00")

    @property
    def balance(self) -> Decimal:
        return max(self.amount - self.repaid, Decimal("0.00"))


class LoanRepayment(models.Model):
    """What a pay period does with a loan: deduct (possibly a changed amount) or skip."""

    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="repayments")
    period = models.ForeignKey(PayPeriod, on_delete=models.CASCADE, related_name="loan_repayments")
    amount = models.DecimalField(**MONEY, default=Decimal("0.00"))
    skipped = models.BooleanField(default=False)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["loan", "period"], name="uniq_loan_period")]


class Saving(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Saving"
        STOPPED = "stopped", "Stopped"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="savings")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="savings")
    amount_per_run = models.DecimalField(**MONEY, validators=POSITIVE)
    start_date = models.DateField()
    note = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["employee__last_name"]

    def __str__(self):
        return f"Saving R{self.amount_per_run} per run for {self.employee}"

    @property
    def balance(self) -> Decimal:
        rows = self.entries.filter(period__status=PayPeriod.Status.APPROVED)
        saved = rows.filter(kind=SavingEntry.Kind.CONTRIBUTION, skipped=False).aggregate(s=Sum("amount"))["s"] or 0
        paid = rows.filter(kind=SavingEntry.Kind.PAYOUT).aggregate(s=Sum("amount"))["s"] or 0
        return Decimal(saved) - Decimal(paid)


class SavingEntry(models.Model):
    class Kind(models.TextChoices):
        CONTRIBUTION = "contribution", "Saved from pay"
        PAYOUT = "payout", "Paid out to employee"

    saving = models.ForeignKey(Saving, on_delete=models.CASCADE, related_name="entries")
    period = models.ForeignKey(PayPeriod, on_delete=models.CASCADE, related_name="saving_entries")
    kind = models.CharField(max_length=15, choices=Kind.choices, default=Kind.CONTRIBUTION)
    amount = models.DecimalField(**MONEY, default=Decimal("0.00"))
    skipped = models.BooleanField(default=False)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["saving", "period", "kind"], name="uniq_saving_period_kind")]
