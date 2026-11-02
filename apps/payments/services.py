"""Batch actions: fill, import amounts, approve, export. Each change is audited."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.core.models import audit
from apps.employees.models import Employee

from . import fnb
from .models import PaymentBatch, PaymentLine


class BatchError(Exception):
    pass


def _require_draft(batch: PaymentBatch):
    if batch.is_locked:
        raise BatchError("This batch is approved and locked. Reopen it first to change it.")


def add_active_employees(batch: PaymentBatch, actor: str) -> tuple[int, list[str]]:
    """Add a R0.00 line for every active employee with bank details. Returns (added, codes missing bank details)."""
    _require_draft(batch)
    existing = set(batch.lines.values_list("employee_id", flat=True))
    employees = Employee.objects.filter(company=batch.company, status=Employee.Status.ACTIVE).select_related(
        "bank_account")
    added, missing = [], []
    for employee in employees:
        if employee.id in existing:
            continue
        if not hasattr(employee, "bank_account"):
            missing.append(employee.employee_code)
            continue
        added.append(PaymentLine.from_employee(batch, employee))
    PaymentLine.objects.bulk_create(added)
    audit("payments.add_employees", actor=actor, company=batch.company, target=str(batch), added=len(added))
    return len(added), missing


@dataclass
class ImportResult:
    updated: int = 0
    added: int = 0
    errors: list[str] = field(default_factory=list)


def _parse_amount(raw: str) -> Decimal:
    text = (raw or "").replace("R", "").replace(" ", "").replace(" ", "")
    if text.count(",") == 1 and "." not in text:
        text = text.replace(",", ".")  # 1234,56 typed the South African way
    text = text.replace(",", "")
    amount = Decimal(text)
    if amount < 0 or amount != amount.quantize(Decimal("0.01")):
        raise InvalidOperation
    return amount


def import_amounts(batch: PaymentBatch, uploaded_text: str, actor: str) -> ImportResult:
    """Set amounts from a CSV with columns employee_code, amount (a heading row is fine).

    Typical source: the net pay report exported from PaySpace. Nothing is
    saved if any row is wrong, so a half-imported batch can't happen.
    """
    _require_draft(batch)
    result = ImportResult()
    rows = list(csv.reader(io.StringIO(uploaded_text.lstrip("﻿"))))
    employees = {e.employee_code.upper(): e for e in Employee.objects.filter(company=batch.company)
                 .select_related("bank_account")}
    lines = {line.employee_id: line for line in batch.lines.all()}
    changes: list[PaymentLine] = []
    new_lines: list[PaymentLine] = []
    for number, row in enumerate(rows, start=1):
        if not row or not any(cell.strip() for cell in row):
            continue
        code = row[0].strip().upper()
        raw_amount = row[1].strip() if len(row) > 1 else ""
        try:
            amount = _parse_amount(raw_amount)
        except (InvalidOperation, ValueError):
            if number == 1:
                continue  # heading row
            result.errors.append(f"Row {number}: '{raw_amount}' is not an amount.")
            continue
        employee = employees.get(code)
        if employee is None:
            result.errors.append(f"Row {number}: no employee with code {code}.")
            continue
        line = lines.get(employee.id)
        if line is None:
            if not hasattr(employee, "bank_account"):
                result.errors.append(f"Row {number}: {code} has no bank details captured.")
                continue
            line = PaymentLine.from_employee(batch, employee, amount)
            new_lines.append(line)
            lines[employee.id] = line
        else:
            line.amount = amount
            changes.append(line)
    if result.errors:
        return result
    with transaction.atomic():
        PaymentLine.objects.bulk_update(changes, ["amount"])
        PaymentLine.objects.bulk_create(new_lines)
    result.updated, result.added = len(changes), len(new_lines)
    audit("payments.import_amounts", actor=actor, company=batch.company, target=str(batch),
          updated=result.updated, added=result.added)
    return result


def remove_zero_lines(batch: PaymentBatch, actor: str) -> int:
    _require_draft(batch)
    removed, _ = batch.lines.filter(amount__lte=0).delete()
    if removed:
        audit("payments.remove_zero", actor=actor, company=batch.company, target=str(batch), removed=removed)
    return removed


def approve(batch: PaymentBatch, user) -> list[fnb.Problem]:
    _require_draft(batch)
    problems = fnb.check(batch)
    if problems:
        return problems
    batch.status = PaymentBatch.Status.APPROVED
    batch.approved_by = user
    batch.approved_at = timezone.now()
    batch.save(update_fields=["status", "approved_by", "approved_at"])
    audit("payments.approve", actor=f"user:{user.get_username()}", company=batch.company, target=str(batch),
          lines=batch.lines.count(), total=str(batch.total))
    return []


def reopen(batch: PaymentBatch, user) -> None:
    batch.status = PaymentBatch.Status.DRAFT
    batch.approved_by = None
    batch.approved_at = None
    batch.save(update_fields=["status", "approved_by", "approved_at"])
    audit("payments.reopen", actor=f"user:{user.get_username()}", company=batch.company, target=str(batch))


def export(batch: PaymentBatch, user) -> tuple[str, str]:
    """Return (file name, file text). Only approved batches can be exported."""
    if batch.status == PaymentBatch.Status.DRAFT:
        raise BatchError("Approve the batch before downloading the FNB file.")
    problems = fnb.check(batch)
    if problems:
        raise BatchError("; ".join(f"{p.employee_code}: {p.message}" if p.employee_code else p.message
                                   for p in problems))
    text = fnb.build_file(batch)
    if batch.status != PaymentBatch.Status.EXPORTED:
        batch.status = PaymentBatch.Status.EXPORTED
        batch.exported_at = timezone.now()
        batch.save(update_fields=["status", "exported_at"])
    audit("payments.export_fnb", actor=f"user:{user.get_username()}", company=batch.company, target=str(batch),
          lines=batch.lines.count(), total=str(batch.total))
    return fnb.file_name(batch), text
