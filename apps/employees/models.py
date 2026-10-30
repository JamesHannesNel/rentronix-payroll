from django.db import models

from apps.core.models import Company


class Site(models.Model):
    """A physical work site, e.g. a construction site with its own Bio3G reader."""

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="sites")
    name = models.CharField(max_length=120)

    class Meta:
        ordering = ["company", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_site_name")]

    def __str__(self):
        return self.name


class Team(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="teams")
    name = models.CharField(max_length=120)

    class Meta:
        ordering = ["company", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_team_name")]

    def __str__(self):
        return self.name


class Employee(models.Model):
    """An employee as the time-and-attendance side needs them.

    For PaySpace-connected clients, PaySpace is the master record and these
    fields are refreshed by `manage.py payspace_pull`. Tax and bank details
    stay in PaySpace and are not copied here.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        TERMINATED = "terminated", "Terminated"

    class Source(models.TextChoices):
        MANUAL = "manual", "Captured in this app"
        PAYSPACE = "payspace", "Synced from PaySpace"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="employees")
    employee_code = models.CharField(max_length=30, help_text="Employee number, shared with PaySpace and Bio3G.")
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    preferred_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    cell_number = models.CharField(max_length=20, blank=True, help_text="Used for WhatsApp/SMS payslips later.")
    job_title = models.CharField(max_length=120, blank=True)
    pay_frequency = models.CharField(max_length=40, blank=True)
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name="employees")
    team = models.ForeignKey(Team, null=True, blank=True, on_delete=models.SET_NULL, related_name="employees")
    date_engaged = models.DateField(null=True, blank=True)
    termination_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL)
    payspace_employee_id = models.CharField(max_length=40, blank=True, db_index=True)

    biometric_enrolled = models.BooleanField(default=False)
    biometric_method = models.CharField(max_length=20, blank=True)
    biometric_checked_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ["company", "last_name", "first_name"]
        constraints = [models.UniqueConstraint(fields=["company", "employee_code"], name="uniq_employee_code")]
        indexes = [models.Index(fields=["company", "status"])]

    def __str__(self):
        return f"{self.employee_code} {self.full_name}"

    @property
    def full_name(self):
        return f"{self.preferred_name or self.first_name} {self.last_name}".strip()
