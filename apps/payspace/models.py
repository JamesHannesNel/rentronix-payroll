from django.db import models

from apps.core.models import Company


class PaySpaceLink(models.Model):
    """Ties one of our companies to a PaySpace company ID."""

    company = models.OneToOneField(Company, on_delete=models.CASCADE, related_name="payspace_link")
    payspace_company_id = models.CharField(max_length=20)
    payspace_company_name = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    last_pulled_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "PaySpace link"

    def __str__(self):
        return f"{self.company} ↔ PaySpace {self.payspace_company_id}"


class PayFrequency(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="pay_frequencies")
    payspace_id = models.CharField(max_length=40)
    name = models.CharField(max_length=100)
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name_plural = "pay frequencies"
        constraints = [models.UniqueConstraint(fields=["company", "payspace_id"], name="uniq_frequency")]

    def __str__(self):
        return self.name


class ComponentCode(models.Model):
    """A PaySpace payroll component code, e.g. OT15 'Overtime at 1.5x'.

    Phase 1 maps clocked hour types (normal, overtime, Sunday, public
    holiday, night shift) to these codes before pushing hours to PaySpace.
    """

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="component_codes")
    code = models.CharField(max_length=60)
    description = models.CharField(max_length=200, blank=True)
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["company", "code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="uniq_component_code")]

    def __str__(self):
        return f"{self.code} – {self.description}" if self.description else self.code


class SyncRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        OK = "ok", "Succeeded"
        FAILED = "failed", "Failed"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="payspace_syncs")
    kind = models.CharField(max_length=30, default="pull")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    counts = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.company} {self.kind} {self.started_at:%Y-%m-%d %H:%M} {self.status}"
