import datetime as dt

import pytest
from rest_framework.test import APIClient

from apps.core.models import ApiKey, AuditLog
from apps.employees.models import Employee
from apps.timeclock.models import ClockRecord, Device

SAST = dt.timezone(dt.timedelta(hours=2))


@pytest.fixture
def sipho(company):
    return Employee.objects.create(company=company, employee_code="EMP0042", first_name="Sipho", last_name="Dlamini")


def clock(record_id="884512", code="EMP0042", at="2026-11-02T06:58:41+02:00", **extra):
    return {"reader_serial": "FA1000-00123", "record_id": record_id, "employee_code": code, "clocked_at": at,
            "direction": "in", "method": "face", **extra}


def test_requires_a_key(db):
    assert APIClient().get("/api/v1/employees").status_code == 401


def test_rejects_wrong_key(company):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer rp_deadbeef_nope")
    assert client.get("/api/v1/employees").status_code == 401


def test_disabled_and_expired_keys_are_refused(company):
    key, raw = ApiKey.issue(company, "Old", [ApiKey.Scope.EMPLOYEE_READ])
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    key.is_active = False
    key.save()
    assert client.get("/api/v1/employees").status_code == 401
    key.is_active = True
    key.expires_at = dt.datetime(2020, 1, 1, tzinfo=SAST)
    key.save()
    assert client.get("/api/v1/employees").status_code == 401


def test_scope_is_enforced(company):
    _, raw = ApiKey.issue(company, "Reports", [ApiKey.Scope.EMPLOYEE_READ])
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    assert client.get("/api/v1/employees").status_code == 200
    assert client.post("/api/v1/clocks", {"clocks": [clock()]}, format="json").status_code == 403


def test_employees_are_limited_to_the_keys_company(api, sipho, other_company):
    Employee.objects.create(company=other_company, employee_code="X1", first_name="Not", last_name="Mine")
    body = api.get("/api/v1/employees").json()
    assert [e["employee_code"] for e in body["results"]] == ["EMP0042"]


def test_employees_updated_since(api, sipho):
    later = (sipho.updated_at + dt.timedelta(seconds=1)).isoformat()
    assert api.get("/api/v1/employees", {"updated_since": later}).json()["results"] == []
    assert api.get("/api/v1/employees", {"updated_since": "yesterday"}).status_code == 400


def test_clock_import_is_idempotent(api, sipho):
    first = api.post("/api/v1/clocks", {"clocks": [clock(), clock("884513", at="2026-11-02T16:01:00+02:00")]},
                     format="json")
    assert first.status_code == 200
    assert first.json()["accepted"] == 2
    again = api.post("/api/v1/clocks", {"clocks": [clock()]}, format="json").json()
    assert again["accepted"] == 0 and again["duplicates"] == 1
    assert ClockRecord.objects.count() == 2
    record = ClockRecord.objects.get(record_id="884512")
    assert record.employee == sipho
    assert record.clocked_at == dt.datetime(2026, 11, 2, 6, 58, 41, tzinfo=SAST)  # device time kept
    assert record.source == ClockRecord.Source.BIO3G
    assert Device.objects.get(serial="FA1000-00123").is_online
    assert AuditLog.objects.filter(action="clocks.import").count() == 1


def test_unknown_employee_codes_are_kept_and_flagged(api, sipho):
    body = api.post("/api/v1/clocks", {"clocks": [clock(code="EMP9999")]}, format="json").json()
    assert body["accepted"] == 1 and body["unmatched_employee"] == 1
    assert ClockRecord.objects.get().employee is None


def test_bad_rows_rejected_good_rows_kept(api, sipho):
    resp = api.post("/api/v1/clocks", {"clocks": [clock(), clock("2", at="not a time")]}, format="json")
    assert resp.status_code == 207
    body = resp.json()
    assert body["accepted"] == 1 and body["rejected"] == 1 and body["errors"][0]["index"] == 1


def test_time_without_offset_is_read_as_sast(api, sipho):
    api.post("/api/v1/clocks", {"clocks": [clock(at="2026-11-02T07:00:00")]}, format="json")
    assert ClockRecord.objects.get().clocked_at == dt.datetime(2026, 11, 2, 7, 0, tzinfo=SAST)


def test_enrolments_and_device_status(api, sipho):
    resp = api.post("/api/v1/enrolments",
                    {"enrolments": [{"employee_code": "EMP0042", "enrolled": True, "method": "face"},
                                    {"employee_code": "NOPE", "enrolled": True}]}, format="json")
    assert resp.json() == {"updated": 1, "unknown": 1}
    sipho.refresh_from_db()
    assert sipho.biometric_enrolled and sipho.biometric_method == "face"

    api.post("/api/v1/devices/status", {"devices": [{"reader_serial": "FA1000-00777", "online": False}]},
             format="json")
    assert Device.objects.get(serial="FA1000-00777").is_online is False


def test_rate_limit(api, settings, sipho):
    from rest_framework.settings import api_settings
    from rest_framework.throttling import SimpleRateThrottle

    original = SimpleRateThrottle.THROTTLE_RATES
    SimpleRateThrottle.THROTTLE_RATES = {**api_settings.DEFAULT_THROTTLE_RATES, "api_key": "3/min"}
    try:
        codes = [api.get("/api/v1/employees").status_code for _ in range(4)]
    finally:
        SimpleRateThrottle.THROTTLE_RATES = original
    assert codes == [200, 200, 200, 429]
