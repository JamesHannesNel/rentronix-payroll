"""An in-process fake of the PaySpace API for development and tests.

With PAYSPACE_ENV=mock the client's HTTP session is routed here instead of
the internet. The data is invented; field names follow PaySpace's API so
the sync code is exercised the same way it will be on staging.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import requests
from requests.adapters import BaseAdapter

MOCK_HOST = "https://mock.payspace.local"
MOCK_COMPANY_ID = 900001
PAGE_SIZE = 4  # small, so paging is exercised

COMPANIES = [
    {"company_id": MOCK_COMPANY_ID, "company_name": "Demo Construction (Pty) Ltd (MOCK)", "company_code": "DEMO"},
]

EMPLOYEES = [
    {"EmployeeId": 7001, "EmployeeNumber": "EMP0001", "FirstName": "Sipho", "LastName": "Dlamini",
     "PreferredName": "", "Email": "", "CellNumber": "0820000001"},
    {"EmployeeId": 7002, "EmployeeNumber": "EMP0002", "FirstName": "Thandeka", "LastName": "Mokoena",
     "PreferredName": "Thandi", "Email": "thandi@example.com", "CellNumber": "0820000002"},
    {"EmployeeId": 7003, "EmployeeNumber": "EMP0003", "FirstName": "Johan", "LastName": "van der Merwe",
     "PreferredName": "", "Email": "", "CellNumber": "0820000003"},
    {"EmployeeId": 7004, "EmployeeNumber": "EMP0004", "FirstName": "Ayanda", "LastName": "Ndlovu",
     "PreferredName": "", "Email": "", "CellNumber": "0820000004"},
    {"EmployeeId": 7005, "EmployeeNumber": "EMP0005", "FirstName": "Priya", "LastName": "Naidoo",
     "PreferredName": "", "Email": "priya@example.com", "CellNumber": "0820000005"},
    {"EmployeeId": 7006, "EmployeeNumber": "EMP0006", "FirstName": "Lerato", "LastName": "Khumalo",
     "PreferredName": "", "Email": "", "CellNumber": "0820000006"},
]

EMPLOYMENT_STATUSES = [
    {"EmployeeNumber": "EMP0001", "EmploymentDate": "2021-02-01T00:00:00", "TerminationDate": None},
    {"EmployeeNumber": "EMP0002", "EmploymentDate": "2019-07-15T00:00:00", "TerminationDate": None},
    {"EmployeeNumber": "EMP0003", "EmploymentDate": "2023-01-09T00:00:00", "TerminationDate": None},
    {"EmployeeNumber": "EMP0004", "EmploymentDate": "2024-03-04T00:00:00", "TerminationDate": None},
    {"EmployeeNumber": "EMP0005", "EmploymentDate": "2022-10-01T00:00:00", "TerminationDate": None},
    {"EmployeeNumber": "EMP0006", "EmploymentDate": "2020-05-18T00:00:00", "TerminationDate": "2026-09-30T00:00:00"},
]

POSITIONS = [
    {"EmployeeNumber": "EMP0001", "Job": "General worker", "PayPoint": "Umhlanga site", "OrganizationRegion": "KZN",
     "PayFrequency": "Weekly"},
    {"EmployeeNumber": "EMP0002", "Job": "Site administrator", "PayPoint": "Head office", "OrganizationRegion": "KZN",
     "PayFrequency": "Monthly"},
    {"EmployeeNumber": "EMP0003", "Job": "Foreman", "PayPoint": "Umhlanga site", "OrganizationRegion": "KZN",
     "PayFrequency": "Weekly"},
    {"EmployeeNumber": "EMP0004", "Job": "General worker", "PayPoint": "Ballito site", "OrganizationRegion": "KZN",
     "PayFrequency": "Weekly"},
    {"EmployeeNumber": "EMP0005", "Job": "Quantity surveyor", "PayPoint": "Head office", "OrganizationRegion": "KZN",
     "PayFrequency": "Monthly"},
    {"EmployeeNumber": "EMP0006", "Job": "Driver", "PayPoint": "Ballito site", "OrganizationRegion": "KZN",
     "PayFrequency": "Weekly"},
]

FREQUENCIES = [
    {"CompanyFrequencyId": 1, "FrequencyName": "Monthly", "RunFrequency": "Monthly"},
    {"CompanyFrequencyId": 2, "FrequencyName": "Weekly", "RunFrequency": "Weekly"},
]

COMPONENT_CODES = [
    {"Value": "BASIC", "Description": "Basic salary / wage"},
    {"Value": "NORMHRS", "Description": "Normal hours"},
    {"Value": "OT15", "Description": "Overtime at 1.5x"},
    {"Value": "OT20", "Description": "Overtime at 2.0x (Sundays)"},
    {"Value": "PHWORK", "Description": "Public holiday worked"},
    {"Value": "SHIFT", "Description": "Night shift allowance"},
]

COMPANY_RUNS = [
    {"CompanyRunId": 501, "FrequencyName": "Monthly", "PeriodStartDate": "2026-10-01T00:00:00",
     "PeriodEndDate": "2026-10-31T00:00:00", "Status": "Open"},
    {"CompanyRunId": 502, "FrequencyName": "Weekly", "PeriodStartDate": "2026-10-26T00:00:00",
     "PeriodEndDate": "2026-11-01T00:00:00", "Status": "Open"},
]


def _collection(path_tail: str):
    routes = {
        "Employee/effective": EMPLOYEES,
        "EmployeeEmploymentStatus/all": EMPLOYMENT_STATUSES,
        "EmployeePosition/all": POSITIONS,
        "CompanyFrequency": FREQUENCIES,
        "Lookup/ComponentCode": COMPONENT_CODES,
        "CompanyRun": COMPANY_RUNS,
    }
    for prefix, rows in routes.items():
        if path_tail == prefix or path_tail.startswith(prefix + "/"):
            return rows
    return None


class MockPaySpaceAdapter(BaseAdapter):
    def send(self, request, **kwargs):
        url = urlparse(request.url)
        if url.path == "/connect/token":
            return self._json(request, 200, {
                "access_token": "mock-token",
                "expires_in": 3600,
                "token_type": "Bearer",
                "group_companies": [{"group_id": 1, "group_description": "Mock group", "companies": COMPANIES}],
            })

        if request.headers.get("Authorization") != "Bearer mock-token":
            return self._json(request, 401, {"error": "unauthorized"})

        parts = url.path.split("/odata/v2.0/", 1)
        if len(parts) != 2:
            return self._json(request, 404, {"error": "not found"})
        company_id, _, tail = parts[1].partition("/")
        if company_id != str(MOCK_COMPANY_ID):
            return self._json(request, 403, {"error": "company not available to these credentials"})
        rows = _collection(tail)
        if rows is None:
            return self._json(request, 404, {"error": f"no mock for {tail}"})

        skip = int(parse_qs(url.query).get("$skip", ["0"])[0])
        page = rows[skip:skip + PAGE_SIZE]
        body = {"@odata.context": f"{MOCK_HOST}/odata/v2.0/$metadata", "value": page}
        if skip + PAGE_SIZE < len(rows):
            body["@odata.nextLink"] = f"{MOCK_HOST}{url.path}?$skip={skip + PAGE_SIZE}"
        return self._json(request, 200, body)

    def close(self):
        pass

    @staticmethod
    def _json(request, status, payload):
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(payload).encode()
        response.headers["Content-Type"] = "application/json"
        response.url = request.url
        response.request = request
        return response


def mount_mock(session: requests.Session) -> None:
    session.mount(MOCK_HOST, MockPaySpaceAdapter())
