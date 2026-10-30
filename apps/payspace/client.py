"""Small PaySpace (Deel Local Payroll) API v2.0 client.

Authentication is OAuth 2.0 client credentials. The token response also
lists the companies the credentials may reach (`group_companies`). Data
endpoints are OData under /odata/v2.0/{company_id}/..., paged with
`@odata.nextLink`.

Endpoint paths follow PaySpace's published v2.0 API (as used by the open
source payspace-sdk). Anything still to be confirmed on staging is marked
"CONFIRM ON STAGING".
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Environment:
    name: str
    token_url: str
    api_base: str


ENVIRONMENTS = {
    "production": Environment(
        "production",
        "https://identity.yourhcm.com/connect/token",
        "https://api.payspace.com/odata/v2.0",
    ),
    "staging": Environment(
        "staging",
        "https://staging-identity.yourhcm.com/connect/token",
        "https://apistaging.payspace.com/odata/v2.0",
    ),
    # In-process fake used for development and tests; see apps/payspace/mock.py.
    "mock": Environment(
        "mock",
        "https://mock.payspace.local/connect/token",
        "https://mock.payspace.local/odata/v2.0",
    ),
}


class PaySpaceError(Exception):
    """Any failure talking to PaySpace, with a message safe to show an admin."""


class PaySpaceClient:
    TOKEN_REFRESH_MARGIN = 300  # seconds before expiry to fetch a new token
    MAX_PAGES = 500  # guard against a nextLink loop

    def __init__(self, env: str, client_id: str, client_secret: str, scope: str = "api.read_only",
                 timeout: int = 30, session: requests.Session | None = None):
        if env not in ENVIRONMENTS:
            raise PaySpaceError(f"Unknown PAYSPACE_ENV '{env}'. Use mock, staging or production.")
        if env != "mock" and not (client_id and client_secret):
            raise PaySpaceError("PAYSPACE_CLIENT_ID and PAYSPACE_CLIENT_SECRET must be set in .env.")
        self.environment = ENVIRONMENTS[env]
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self.timeout = timeout
        self.session = session or requests.Session()
        if env == "mock":
            from .mock import mount_mock

            mount_mock(self.session)
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._companies: list[dict] = []

    @classmethod
    def from_settings(cls, session: requests.Session | None = None) -> PaySpaceClient:
        from django.conf import settings

        cfg = settings.PAYSPACE
        return cls(cfg["ENV"], cfg["CLIENT_ID"], cfg["CLIENT_SECRET"], cfg["SCOPE"], cfg["TIMEOUT"], session)

    # ---- authentication -------------------------------------------------

    def authenticate(self) -> None:
        try:
            response = self.session.post(
                self.environment.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": self.scope,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise PaySpaceError(f"Could not reach PaySpace sign-in ({self.environment.token_url}): {exc}") from exc
        if response.status_code in (400, 401):
            raise PaySpaceError(
                f"PaySpace rejected the credentials (HTTP {response.status_code}). "
                "Check PAYSPACE_CLIENT_ID, PAYSPACE_CLIENT_SECRET and PAYSPACE_SCOPE."
            )
        if not response.ok:
            raise PaySpaceError(f"PaySpace sign-in failed with HTTP {response.status_code}.")
        body = response.json()
        self._token = body["access_token"]
        self._token_expires_at = time.monotonic() + int(body.get("expires_in", 3600))
        self._companies = [
            company
            for group in body.get("group_companies", []) or []
            for company in group.get("companies", []) or []
        ]
        self.session.headers["Authorization"] = f"Bearer {self._token}"

    def _ensure_token(self) -> None:
        if not self._token or time.monotonic() >= self._token_expires_at - self.TOKEN_REFRESH_MARGIN:
            self.authenticate()

    def companies(self) -> list[dict]:
        """Companies these credentials can reach, from the token response."""
        self._ensure_token()
        return list(self._companies)

    # ---- data -----------------------------------------------------------

    def get(self, path: str, params: dict | None = None) -> list[dict]:
        """GET an OData collection and follow paging. Returns all rows."""
        self._ensure_token()
        url = f"{self.environment.api_base}/{path.lstrip('/')}"
        rows: list[dict] = []
        for _ in range(self.MAX_PAGES):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                raise PaySpaceError(f"Could not reach PaySpace ({path}): {exc}") from exc
            if response.status_code == 401:
                # Token revoked or expired early: sign in once more and retry this page.
                self.authenticate()
                response = self.session.get(url, params=params, timeout=self.timeout)
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "5"))
                logger.warning("PaySpace rate limit hit; waiting %ss", retry_after)
                time.sleep(min(retry_after, 60))
                continue
            if not response.ok:
                raise PaySpaceError(f"PaySpace returned HTTP {response.status_code} for {path}.")
            body = response.json()
            if isinstance(body, list):
                rows.extend(body)
                break
            rows.extend(body.get("value", []))
            next_link = body.get("@odata.nextLink")
            if not next_link:
                break
            url, params = next_link, None
        else:
            raise PaySpaceError(f"Too many pages from PaySpace for {path}.")
        return rows

    def employees(self, company_id: int | str, effective: dt.date) -> list[dict]:
        return self.get(f"{company_id}/Employee/effective/{effective:%Y-%m-%d}")

    def employment_statuses(self, company_id: int | str) -> list[dict]:
        return self.get(f"{company_id}/EmployeeEmploymentStatus/all")

    def positions(self, company_id: int | str) -> list[dict]:
        return self.get(f"{company_id}/EmployeePosition/all")

    def pay_frequencies(self, company_id: int | str) -> list[dict]:
        return self.get(f"{company_id}/CompanyFrequency")

    def component_codes(self, company_id: int | str) -> list[dict]:
        return self.get(f"{company_id}/Lookup/ComponentCode")

    def company_runs(self, company_id: int | str) -> list[dict]:
        return self.get(f"{company_id}/CompanyRun")
