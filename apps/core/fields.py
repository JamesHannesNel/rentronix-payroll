"""Encrypted model fields for personal information (POPIA).

Values are encrypted with Fernet (AES-128-CBC + HMAC) using the key in
FIELD_ENCRYPTION_KEY before they reach the database, so a copied database
or backup on its own does not expose bank account numbers.

Keep FIELD_ENCRYPTION_KEY safe and backed up with .env: without it the
encrypted values cannot be read again. Encrypted fields cannot be searched
or filtered in the database.
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

PREFIX = "enc1:"


@lru_cache(maxsize=4)
def _fernet(key: str) -> Fernet:
    if not key:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY is not set. scripts/setup-ubuntu.sh creates one in .env.")
    try:
        return Fernet(key.encode())
    except ValueError as exc:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY is not a valid Fernet key.") from exc


def encrypt(value: str) -> str:
    return PREFIX + _fernet(settings.FIELD_ENCRYPTION_KEY).encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not value.startswith(PREFIX):
        return value  # plain value written before encryption was switched on
    try:
        return _fernet(settings.FIELD_ENCRYPTION_KEY).decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken as exc:
        raise ImproperlyConfigured("Could not decrypt a stored value: FIELD_ENCRYPTION_KEY has changed.") from exc


class EncryptedCharField(models.TextField):
    """A short text value stored encrypted. `max_length` is checked on the plain value."""

    def __init__(self, *args, plain_max_length=None, **kwargs):
        self.plain_max_length = plain_max_length
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if self.plain_max_length is not None:
            kwargs["plain_max_length"] = self.plain_max_length
        return name, path, args, kwargs

    def from_db_value(self, value, expression, connection):
        return value if value in (None, "") else decrypt(value)

    def to_python(self, value):
        return value if value in (None, "") or not isinstance(value, str) else decrypt(value)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return value if value in (None, "") else encrypt(str(value))

    def formfield(self, **kwargs):
        from django import forms

        defaults = {"form_class": forms.CharField, "max_length": self.plain_max_length}
        defaults.update(kwargs)
        field = super().formfield(**defaults)
        field.widget = forms.TextInput()
        return field
