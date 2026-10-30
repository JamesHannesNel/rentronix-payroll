"""Bearer-key authentication for the integration API.

Partners send `Authorization: Bearer rp_<prefix>_<secret>`. The key decides
which company the request acts for, and its scopes decide what it may do.
"""

import hmac

from django.utils import timezone
from rest_framework import authentication, exceptions, permissions, throttling

from .models import ApiKey


class ApiKeyAuthentication(authentication.BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode("latin-1")
        if not header:
            return None
        parts = header.split()
        if len(parts) != 2 or parts[0] != self.keyword:
            raise exceptions.AuthenticationFailed("Use 'Authorization: Bearer <api key>'.")
        raw_key = parts[1]
        try:
            _, prefix, _ = raw_key.split("_", 2)
        except ValueError:
            raise exceptions.AuthenticationFailed("Malformed API key.") from None

        api_key = ApiKey.objects.select_related("company").filter(prefix=prefix).first()
        if api_key is None or not hmac.compare_digest(api_key.key_hash, ApiKey.hash_key(raw_key)):
            raise exceptions.AuthenticationFailed("Invalid API key.")
        if not api_key.is_active or not api_key.company.is_active:
            raise exceptions.AuthenticationFailed("API key is disabled.")
        if api_key.expires_at and api_key.expires_at <= timezone.now():
            raise exceptions.AuthenticationFailed("API key has expired.")

        ApiKey.objects.filter(pk=api_key.pk).update(last_used_at=timezone.now())
        return (None, api_key)

    def authenticate_header(self, request):
        return self.keyword


class HasApiKey(permissions.BasePermission):
    """Requires a valid key and, if the view sets `required_scope`, that scope."""

    def has_permission(self, request, view):
        api_key = request.auth
        if not isinstance(api_key, ApiKey):
            return False
        scope = getattr(view, "required_scopes", {}).get(request.method)
        if scope and not api_key.has_scope(scope):
            raise exceptions.PermissionDenied(f"This API key lacks the '{scope}' scope.")
        return True


class PerKeyRateThrottle(throttling.SimpleRateThrottle):
    scope = "api_key"

    def get_cache_key(self, request, view):
        if isinstance(request.auth, ApiKey):
            return f"throttle_api_key_{request.auth.pk}"
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}
