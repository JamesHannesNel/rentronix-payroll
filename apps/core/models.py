import hashlib
import secrets

from django.conf import settings
from django.db import models


class Company(models.Model):
    """A Rentronix client company. Every record in the app belongs to one."""

    name = models.CharField(max_length=200)
    trading_name = models.CharField(max_length=200, blank=True)
    paye_reference = models.CharField("PAYE reference", max_length=20, blank=True)
    uif_reference = models.CharField("UIF reference", max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "companies"

    def __str__(self):
        return self.name


class Membership(models.Model):
    """Which companies a user may see, and in what role."""

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        PAYROLL_ADMIN = "payroll_admin", "Payroll administrator"
        SUPERVISOR = "supervisor", "Supervisor"
        VIEWER = "viewer", "Viewer"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VIEWER)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "company"], name="uniq_membership")]

    def __str__(self):
        return f"{self.user} @ {self.company} ({self.get_role_display()})"


class ApiKey(models.Model):
    """An integration key for one company (e.g. Bio3G's link for that client).

    Only a SHA-256 hash of the key is stored. The full key is shown once,
    when it is created, the same way AllWage handles its External API tokens.
    """

    class Scope(models.TextChoices):
        EMPLOYEE_READ = "employee:read", "View employees"
        CLOCK_READ = "clock:read", "View clock records"
        CLOCK_WRITE = "clock:write", "Import clock records"
        DEVICE_WRITE = "device:write", "Report device status and enrolments"

    PREFIX_LEN = 8

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="api_keys")
    name = models.CharField(max_length=100, help_text="Which system uses this key, e.g. 'Bio3G'.")
    prefix = models.CharField(max_length=PREFIX_LEN, unique=True, editable=False)
    key_hash = models.CharField(max_length=64, editable=False)
    scopes = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["company", "name"]

    def __str__(self):
        return f"{self.name} ({self.prefix}…)"

    @staticmethod
    def hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode()).hexdigest()

    @classmethod
    def issue(cls, company, name, scopes, expires_at=None):
        """Create a key and return (api_key, raw_key). Show raw_key once only."""
        while True:
            prefix = secrets.token_hex(cls.PREFIX_LEN // 2)
            if not cls.objects.filter(prefix=prefix).exists():
                break
        raw_key = f"rp_{prefix}_{secrets.token_urlsafe(32)}"
        api_key = cls.objects.create(
            company=company,
            name=name,
            prefix=prefix,
            key_hash=cls.hash_key(raw_key),
            scopes=list(scopes),
            expires_at=expires_at,
        )
        return api_key, raw_key

    def has_scope(self, scope: str) -> bool:
        return scope in (self.scopes or [])


class AuditLog(models.Model):
    """Append-only record of who changed what. Required for POPIA accountability."""

    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    actor = models.CharField(max_length=150, help_text="User name, 'api:<key prefix>' or 'system:<job>'.")
    action = models.CharField(max_length=50)
    target = models.CharField(max_length=200, blank=True)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor} {self.action} {self.target}"


def audit(action, *, actor, company=None, target="", **detail):
    return AuditLog.objects.create(company=company, actor=actor, action=action, target=str(target), detail=detail)
