"""Read-only pull from PaySpace into the app.

PaySpace stays the master for PaySpace-connected clients. This pull copies
what time and attendance needs: who works where, on which pay frequency,
plus the company's component codes for mapping hours later.

Field names are matched against a short list of candidates because the
exact names are still to be confirmed against staging. `payspace_check`
prints the field names PaySpace actually returns so this map can be fixed.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.core.models import audit
from apps.employees.models import Employee, Site

from .client import PaySpaceClient, PaySpaceError
from .models import ComponentCode, PayFrequency, PaySpaceLink, SyncRun

logger = logging.getLogger(__name__)

EMPLOYEE_FIELDS = {
    "payspace_id": ["EmployeeId", "EmployeeID", "Id"],
    "employee_code": ["EmployeeNumber", "EmpNumber", "EmployeeCode"],
    "first_name": ["FirstName", "FirstNames", "Firstname"],
    "last_name": ["LastName", "Surname", "Lastname"],
    "preferred_name": ["PreferredName", "KnownAs"],
    "email": ["Email", "EmailAddress", "WorkEmail"],
    "cell_number": ["CellNumber", "CellphoneNumber", "MobileNumber"],
}
STATUS_FIELDS = {
    "employee_code": ["EmployeeNumber"],
    "date_engaged": ["EmploymentDate", "GroupJoinDate", "DateEngaged"],
    "termination_date": ["TerminationDate"],
}
POSITION_FIELDS = {
    "employee_code": ["EmployeeNumber"],
    "job_title": ["Job", "JobTitle", "OrganizationPosition", "Position"],
    "site": ["PayPoint", "Branch", "Location"],
    "pay_frequency": ["PayFrequency", "FrequencyName", "Frequency"],
}
FREQUENCY_FIELDS = {"id": ["CompanyFrequencyId", "FrequencyId", "Id", "Value"], "name": ["FrequencyName", "Name", "Description"]}
COMPONENT_FIELDS = {"code": ["Value", "ComponentCode", "Code"], "description": ["Description", "ComponentDescription", "Name"]}


def pick(row: dict, candidates: list[str]):
    for name in candidates:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


def as_date(value) -> dt.date | None:
    if not value:
        return None
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


@dataclass
class PullResult:
    created: int = 0
    updated: int = 0
    terminated: int = 0
    skipped: int = 0
    frequencies: int = 0
    component_codes: int = 0
    warnings: list[str] = field(default_factory=list)

    def counts(self) -> dict:
        return {
            "employees_created": self.created,
            "employees_updated": self.updated,
            "employees_terminated": self.terminated,
            "employees_skipped": self.skipped,
            "pay_frequencies": self.frequencies,
            "component_codes": self.component_codes,
        }


def _optional(fetch, label: str, result: PullResult) -> list[dict]:
    """Supporting data: a failure is a warning, not a failed pull."""
    try:
        return fetch()
    except PaySpaceError as exc:
        result.warnings.append(f"{label}: {exc}")
        logger.warning("PaySpace %s not pulled: %s", label, exc)
        return []


def pull_company(link: PaySpaceLink, client: PaySpaceClient | None = None,
                 effective: dt.date | None = None, actor: str = "system:payspace_pull") -> PullResult:
    client = client or PaySpaceClient.from_settings()
    effective = effective or timezone.localdate()
    company = link.company
    run = SyncRun.objects.create(company=company, kind="pull")
    result = PullResult()
    try:
        ps_id = link.payspace_company_id
        employees = client.employees(ps_id, effective)  # required: failure aborts the pull
        statuses = {pick(r, STATUS_FIELDS["employee_code"]): r
                    for r in _optional(lambda: client.employment_statuses(ps_id), "employment statuses", result)}
        positions = {pick(r, POSITION_FIELDS["employee_code"]): r
                     for r in _optional(lambda: client.positions(ps_id), "positions", result)}
        frequencies = _optional(lambda: client.pay_frequencies(ps_id), "pay frequencies", result)
        components = _optional(lambda: client.component_codes(ps_id), "component codes", result)

        with transaction.atomic():
            _apply_employees(company, employees, statuses, positions, result)
            _apply_frequencies(company, frequencies, result)
            _apply_components(company, components, result)
            link.last_pulled_at = timezone.now()
            link.save(update_fields=["last_pulled_at"])

        run.status = SyncRun.Status.OK
        audit("payspace.pull", actor=actor, company=company, target=f"PaySpace {ps_id}", **result.counts())
    except Exception as exc:
        run.status = SyncRun.Status.FAILED
        run.error = str(exc)
        audit("payspace.pull_failed", actor=actor, company=company, target=f"PaySpace {link.payspace_company_id}",
              error=str(exc))
        raise
    finally:
        run.finished_at = timezone.now()
        run.counts = result.counts()
        run.warnings = result.warnings
        run.save()
    return result


def _apply_employees(company, rows, statuses, positions, result: PullResult) -> None:
    sites: dict[str, Site] = {}
    for row in rows:
        code = pick(row, EMPLOYEE_FIELDS["employee_code"])
        first = pick(row, EMPLOYEE_FIELDS["first_name"])
        last = pick(row, EMPLOYEE_FIELDS["last_name"])
        if not code or not (first or last):
            result.skipped += 1
            continue
        code = str(code).strip()
        status_row = statuses.get(code, {})
        position_row = positions.get(code, {})

        termination = as_date(pick(status_row, STATUS_FIELDS["termination_date"]))
        values = {
            "first_name": (first or "").strip(),
            "last_name": (last or "").strip(),
            "preferred_name": (pick(row, EMPLOYEE_FIELDS["preferred_name"]) or "").strip(),
            "email": (pick(row, EMPLOYEE_FIELDS["email"]) or "").strip(),
            "cell_number": str(pick(row, EMPLOYEE_FIELDS["cell_number"]) or "").strip(),
            "payspace_employee_id": str(pick(row, EMPLOYEE_FIELDS["payspace_id"]) or ""),
            "source": Employee.Source.PAYSPACE,
            "date_engaged": as_date(pick(status_row, STATUS_FIELDS["date_engaged"])),
            "termination_date": termination,
            "status": Employee.Status.TERMINATED
            if termination and termination <= timezone.localdate() else Employee.Status.ACTIVE,
        }
        if position_row:
            values["job_title"] = str(pick(position_row, POSITION_FIELDS["job_title"]) or "")[:120]
            values["pay_frequency"] = str(pick(position_row, POSITION_FIELDS["pay_frequency"]) or "")[:40]
            site_name = pick(position_row, POSITION_FIELDS["site"])
            if site_name:
                site_name = str(site_name)[:120]
                if site_name not in sites:
                    sites[site_name], _ = Site.objects.get_or_create(company=company, name=site_name)
                values["site"] = sites[site_name]

        employee, created = Employee.objects.get_or_create(company=company, employee_code=code, defaults=values)
        if created:
            result.created += 1
        else:
            changed = [name for name, value in values.items() if getattr(employee, name) != value]
            if changed:
                for name in changed:
                    setattr(employee, name, values[name])
                employee.save()
                result.updated += 1
        if values["status"] == Employee.Status.TERMINATED:
            result.terminated += 1


def _apply_frequencies(company, rows, result: PullResult) -> None:
    for row in rows:
        fid, name = pick(row, FREQUENCY_FIELDS["id"]), pick(row, FREQUENCY_FIELDS["name"])
        if fid is None or not name:
            continue
        PayFrequency.objects.update_or_create(
            company=company, payspace_id=str(fid), defaults={"name": str(name)[:100], "raw": row})
        result.frequencies += 1


def _apply_components(company, rows, result: PullResult) -> None:
    for row in rows:
        code = pick(row, COMPONENT_FIELDS["code"])
        if not code:
            continue
        ComponentCode.objects.update_or_create(
            company=company, code=str(code)[:60],
            defaults={"description": str(pick(row, COMPONENT_FIELDS["description"]) or "")[:200], "raw": row})
        result.component_codes += 1
