from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models

from apps.core.fields import EncryptedCharField
from apps.core.models import Company
from apps.employees.models import Employee

# Universal branch codes accepted for any branch of each bank.
UNIVERSAL_BRANCH_CODES = {
    "absa": ("ABSA", "632005"),
    "african_bank": ("African Bank", "430000"),
    "bidvest": ("Bidvest Bank", "462005"),
    "capitec": ("Capitec", "470010"),
    "discovery": ("Discovery Bank", "679000"),
    "fnb": ("FNB", "250655"),
    "investec": ("Investec", "580105"),
    "nedbank": ("Nedbank", "198765"),
    "standard_bank": ("Standard Bank", "051001"),
    "tymebank": ("TymeBank", "678910"),
}
BANK_CHOICES = [(key, name) for key, (name, _) in UNIVERSAL_BRANCH_CODES.items()] + [("other", "Other")]

digits_only = RegexValidator(r"^\d+$", "Digits only, no spaces or dashes.")
branch_code_validator = RegexValidator(r"^\d{6}$", "A branch code is 6 digits.")


class AccountType(models.TextChoices):
    # Values are FNB's codes for the RECIPIENT ACCOUNT TYPE column.
    CURRENT = "1", "Current / cheque"
    SAVINGS = "2", "Savings"
    TRANSMISSION = "3", "Transmission"


class CompanyBankAccount(models.Model):
    """The company account salaries are paid FROM (FNB's 'nominated account')."""

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="bank_accounts")
    name = models.CharField(max_length=60, default="Salaries", help_text="e.g. 'FNB Business Cheque'")
    account_number = EncryptedCharField(plain_max_length=20, validators=[digits_only])
    is_default = models.BooleanField(default=True)

    class Meta:
        verbose_name = "company paying account"

    def __str__(self):
        return f"{self.name} (…{self.account_number[-4:]})" if self.account_number else self.name


class EmployeeBankAccount(models.Model):
    """Where an employee's pay goes. The account number is stored encrypted."""

    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name="bank_account")
    bank = models.CharField(max_length=20, choices=BANK_CHOICES)
    branch_code = models.CharField(max_length=6, validators=[branch_code_validator], blank=True,
                                   help_text="Leave blank to use the bank's universal branch code.")
    account_number = EncryptedCharField(plain_max_length=20, validators=[digits_only])
    account_type = models.CharField(max_length=1, choices=AccountType.choices, default=AccountType.SAVINGS)
    account_holder = models.CharField(max_length=60, blank=True, help_text="Leave blank if it is the employee.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "employee bank account"

    def __str__(self):
        return f"{self.get_bank_display()} …{self.account_number[-4:]}" if self.account_number else self.get_bank_display()

    def clean(self):
        if self.bank == "other" and not self.branch_code:
            raise ValidationError({"branch_code": "Enter the branch code for this bank."})

    def save(self, *args, **kwargs):
        if not self.branch_code and self.bank in UNIVERSAL_BRANCH_CODES:
            self.branch_code = UNIVERSAL_BRANCH_CODES[self.bank][1]
        super().save(*args, **kwargs)


class PaymentBatch(models.Model):
    """One pay run's worth of payments, exported as one FNB import file."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft: amounts can change"
        APPROVED = "approved", "Approved: locked"
        EXPORTED = "exported", "Exported to FNB file"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="payment_batches")
    paying_account = models.ForeignKey(CompanyBankAccount, on_delete=models.PROTECT, related_name="batches")
    name = models.CharField(max_length=60, help_text="e.g. 'Fortnight 1–15 Nov 2026'")
    action_date = models.DateField(help_text="The date staff should receive the money.")
    own_reference = models.CharField(max_length=15, default="SALARIES",
                                     help_text="Shows on the company's statement. Max 15 characters.")
    recipient_reference = models.CharField(max_length=20, blank=True,
                                           help_text="Shows on staff statements. Max 20. Blank = company name.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    exported_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-action_date", "-id"]
        verbose_name_plural = "payment batches"

    def __str__(self):
        return f"{self.company} – {self.name}"

    @property
    def total(self) -> Decimal:
        return sum((line.amount for line in self.lines.all()), Decimal("0.00"))

    @property
    def is_locked(self) -> bool:
        return self.status != self.Status.DRAFT


class PaymentLine(models.Model):
    """One payment to one employee. Bank details are copied in when the line is
    created, so a later change to the employee's account can't alter an approved batch."""

    batch = models.ForeignKey(PaymentBatch, on_delete=models.CASCADE, related_name="lines")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payment_lines")
    recipient_name = models.CharField(max_length=60)
    account_number = EncryptedCharField(plain_max_length=20, validators=[digits_only])
    branch_code = models.CharField(max_length=6, validators=[branch_code_validator])
    account_type = models.CharField(max_length=1, choices=AccountType.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"),
                                 validators=[MinValueValidator(Decimal("0.00"))])
    note = models.CharField(max_length=120, blank=True, help_text="Internal note, not sent to the bank.")

    class Meta:
        ordering = ["employee__employee_code"]
        constraints = [models.UniqueConstraint(fields=["batch", "employee"], name="uniq_batch_employee")]

    def __str__(self):
        return f"{self.employee.employee_code} R{self.amount}"

    @classmethod
    def from_employee(cls, batch, employee, amount=Decimal("0.00")):
        account = employee.bank_account
        return cls(
            batch=batch,
            employee=employee,
            recipient_name=(account.account_holder or employee.full_name)[:60],
            account_number=account.account_number,
            branch_code=account.branch_code,
            account_type=account.account_type,
            amount=amount,
        )
