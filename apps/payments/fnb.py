"""FNB Online Banking payment import file ("BInSol - U ver 1.00" CSV).

Layout, from FNB's own Payment CSV Template and its import guide:

    line 1  BInSol - U ver 1.00
    line 2  payment action date, DD-MM-YYYY
    line 3  nominated (paying) account number, hash total
    line 4  column headings (36 columns)
    line 5+ one payment per line

Every line carries 36 columns, separated by commas, ending in CRLF.
Payment columns used: recipient name, recipient account (max 20),
account type (1 current, 2 savings, 3 transmission), branch code (6),
amount, own reference (max 15), recipient reference (max 20).
The notification columns are left empty.

The hash total is optional and left blank; FNB checks the file when it is
imported and lists any rejected lines in the import results.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from decimal import Decimal

from .models import AccountType, PaymentBatch

FILE_ID = "BInSol - U ver 1.00"
HEADINGS = [
    "RECIPIENT NAME", "RECIPIENT ACCOUNT", "RECIPIENT ACCOUNT TYPE", "BRANCHCODE", "AMOUNT", "OWN REFERENCE",
    "RECIPIENT REFERENCE",
    *[f"EMAIL {i} {part}" for i in range(1, 6) for part in ("NOTIFY", "ADDRESS", "SUBJECT")],
    *[f"FAX {i} {part}" for i in range(1, 3) for part in ("NOTIFY", "CODE", "NUMBER", "SUBJECT")],
    *[f"SMS {i} {part}" for i in range(1, 3) for part in ("NOTIFY", "CODE", "NUMBER")],
]
COLUMNS = len(HEADINGS)  # 36

_UNSAFE = re.compile(r"[^A-Za-z0-9 .\-/&']")


def clean_text(value: str, limit: int) -> str:
    """Bank-safe text: no commas or quotes (they would break FNB's parser), trimmed to the limit."""
    return re.sub(r"\s+", " ", _UNSAFE.sub(" ", value or "")).strip()[:limit]


def _row(*values) -> list[str]:
    row = [str(v) for v in values]
    return row + [""] * (COLUMNS - len(row))


@dataclass
class Problem:
    employee_code: str
    message: str


def check(batch: PaymentBatch) -> list[Problem]:
    """Everything that would make FNB reject a line, or pay the wrong amount."""
    problems: list[Problem] = []
    lines = list(batch.lines.select_related("employee"))
    if not lines:
        problems.append(Problem("", "The batch has no payments."))
    acct = batch.paying_account.account_number
    if not acct or not acct.isdigit() or len(acct) > 20:
        problems.append(Problem("", "The paying account number must be up to 20 digits."))
    seen_accounts: dict[tuple[str, str], str] = {}
    for line in lines:
        code = line.employee.employee_code
        if line.amount <= 0:
            problems.append(Problem(code, "Amount must be more than R0.00 (remove the line if not paying)."))
        if line.amount != line.amount.quantize(Decimal("0.01")):
            problems.append(Problem(code, "Amount has more than 2 decimals."))
        if not line.account_number.isdigit() or len(line.account_number) > 20:
            problems.append(Problem(code, "Account number must be up to 20 digits."))
        if not (line.branch_code.isdigit() and len(line.branch_code) == 6):
            problems.append(Problem(code, "Branch code must be 6 digits."))
        if line.account_type not in AccountType.values:
            problems.append(Problem(code, "Unknown account type."))
        if not clean_text(line.recipient_name, 60):
            problems.append(Problem(code, "Recipient name is empty."))
        key = (line.branch_code, line.account_number)
        if key in seen_accounts:
            problems.append(Problem(code, f"Same bank account as {seen_accounts[key]}: check before paying."))
        seen_accounts[key] = code
    return problems


def build_file(batch: PaymentBatch) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(_row(FILE_ID))
    writer.writerow(_row(batch.action_date.strftime("%d-%m-%Y")))
    writer.writerow(_row(batch.paying_account.account_number, ""))
    writer.writerow(HEADINGS)
    own_ref = clean_text(batch.own_reference, 15)
    recipient_ref = clean_text(batch.recipient_reference or batch.company.trading_name or batch.company.name, 20)
    for line in batch.lines.select_related("employee").order_by("employee__employee_code"):
        writer.writerow(_row(
            clean_text(line.recipient_name, 60),
            line.account_number,
            line.account_type,
            line.branch_code,
            f"{line.amount:.2f}",
            own_ref,
            recipient_ref,
        ))
    return out.getvalue()


def file_name(batch: PaymentBatch) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", batch.name).strip("-")[:40] or "payments"
    return f"FNB-{batch.action_date:%Y%m%d}-{slug}.csv"
