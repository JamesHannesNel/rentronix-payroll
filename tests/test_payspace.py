import datetime as dt

import pytest
import responses
from django.core.management import call_command

from apps.employees.models import Employee, Site
from apps.payspace.client import ENVIRONMENTS, PaySpaceClient, PaySpaceError
from apps.payspace.mock import MOCK_COMPANY_ID
from apps.payspace.models import ComponentCode, PayFrequency, PaySpaceLink, SyncRun
from apps.payspace.sync import pull_company

TODAY = dt.date(2026, 10, 30)


@pytest.fixture
def link(company):
    return PaySpaceLink.objects.create(company=company, payspace_company_id=str(MOCK_COMPANY_ID))


def test_mock_client_signs_in_and_lists_companies():
    client = PaySpaceClient("mock", "", "")
    assert client.companies()[0]["company_id"] == MOCK_COMPANY_ID


def test_mock_client_follows_paging():
    rows = PaySpaceClient("mock", "", "").employees(MOCK_COMPANY_ID, TODAY)
    assert len(rows) == 6  # mock pages hold 4, so this needed two pages


def test_real_environments_require_credentials():
    with pytest.raises(PaySpaceError, match="PAYSPACE_CLIENT_ID"):
        PaySpaceClient("staging", "", "")


@responses.activate
def test_staging_token_request_and_bad_credentials():
    env = ENVIRONMENTS["staging"]
    responses.post(env.token_url, status=400, json={"error": "invalid_client"})
    client = PaySpaceClient("staging", "id", "secret")
    with pytest.raises(PaySpaceError, match="rejected the credentials"):
        client.companies()
    body = responses.calls[0].request.body
    assert "grant_type=client_credentials" in body and "scope=api.read_only" in body


@responses.activate
def test_staging_reauthenticates_once_on_401():
    env = ENVIRONMENTS["staging"]
    responses.post(env.token_url, json={"access_token": "t1", "expires_in": 3600, "group_companies": []})
    url = f"{env.api_base}/1/CompanyFrequency"
    responses.get(url, status=401)
    responses.get(url, json={"value": [{"FrequencyName": "Monthly"}]})
    rows = PaySpaceClient("staging", "id", "secret").pay_frequencies(1)
    assert rows == [{"FrequencyName": "Monthly"}]
    assert sum(1 for c in responses.calls if c.request.url == env.token_url) == 2


def test_pull_creates_employees_sites_codes(link):
    result = pull_company(link, effective=TODAY)
    assert result.created == 6 and result.updated == 0 and result.skipped == 0
    assert Employee.objects.filter(company=link.company).count() == 6
    thandi = Employee.objects.get(employee_code="EMP0002")
    assert thandi.full_name == "Thandi Mokoena"
    assert thandi.pay_frequency == "Monthly"
    assert thandi.date_engaged == dt.date(2019, 7, 15)
    assert Employee.objects.get(employee_code="EMP0006").status == Employee.Status.TERMINATED
    assert set(Site.objects.values_list("name", flat=True)) == {"Umhlanga site", "Head office", "Ballito site"}
    assert ComponentCode.objects.filter(company=link.company, code="OT15").exists()
    assert PayFrequency.objects.filter(company=link.company).count() == 2
    assert SyncRun.objects.get().status == SyncRun.Status.OK


def test_second_pull_changes_nothing(link):
    pull_company(link, effective=TODAY)
    again = pull_company(link, effective=TODAY)
    assert again.created == 0 and again.updated == 0


def test_pull_for_unavailable_company_fails_cleanly(company):
    bad = PaySpaceLink.objects.create(company=company, payspace_company_id="123")
    with pytest.raises(PaySpaceError):
        pull_company(bad, effective=TODAY)
    run = SyncRun.objects.get()
    assert run.status == SyncRun.Status.FAILED and "403" in run.error
    assert not Employee.objects.exists()


def test_check_command_prints_field_names_not_values(db, capsys):
    call_command("payspace_check")
    out = capsys.readouterr().out
    assert "All checks passed" in out
    assert "EmployeeNumber" in out
    assert "Sipho" not in out and "0820000001" not in out


def test_link_then_pull_commands(db, capsys):
    call_command("payspace_link", payspace_company=str(MOCK_COMPANY_ID))
    call_command("payspace_pull")
    assert "employees created 6" in capsys.readouterr().out
