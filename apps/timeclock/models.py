from django.db import models

from apps.core.models import Company
from apps.employees.models import Employee, Site


class Device(models.Model):
    """A clocking device, e.g. a Bio3G FA1000 reader on a site."""

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="devices")
    serial = models.CharField(max_length=60)
    name = models.CharField(max_length=120, blank=True)
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name="devices")
    is_online = models.BooleanField(default=False)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    firmware = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ["company", "serial"]
        constraints = [models.UniqueConstraint(fields=["company", "serial"], name="uniq_device_serial")]

    def __str__(self):
        return self.name or self.serial


class ClockRecord(models.Model):
    """One clock-in or clock-out, exactly as the device recorded it.

    `clocked_at` is the device's own timestamp, so a record uploaded hours
    late after a signal outage keeps its true time. Records whose employee
    code we don't know are kept (employee empty) and shown as exceptions,
    never dropped.
    """

    class Direction(models.TextChoices):
        IN = "in", "In"
        OUT = "out", "Out"
        UNKNOWN = "unknown", "Unknown"

    class Source(models.TextChoices):
        BIO3G = "bio3g", "Bio3G reader"
        API = "api", "Other API partner"
        MANUAL = "manual", "Captured manually"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="clock_records")
    employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.PROTECT, related_name="clocks")
    employee_code = models.CharField(max_length=30, help_text="As sent by the device.")
    device = models.ForeignKey(Device, null=True, blank=True, on_delete=models.SET_NULL, related_name="clocks")
    reader_serial = models.CharField(max_length=60)
    record_id = models.CharField(max_length=60, help_text="The device's own ID for this record.")
    clocked_at = models.DateTimeField(db_index=True)
    direction = models.CharField(max_length=10, choices=Direction.choices, default=Direction.UNKNOWN)
    method = models.CharField(max_length=20, blank=True, help_text="face, finger, card, pin…")
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.BIO3G)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-clocked_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "reader_serial", "record_id"], name="uniq_clock_record"),
        ]
        indexes = [models.Index(fields=["company", "employee", "clocked_at"])]

    def __str__(self):
        return f"{self.employee_code} {self.direction} {self.clocked_at:%Y-%m-%d %H:%M}"
