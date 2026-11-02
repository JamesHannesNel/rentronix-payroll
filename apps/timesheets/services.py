"""Changes to a pay period: adjustments, LOA nights, loan choices, approve, export."""

from __future__ import annotations

import csv
import io
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.models import audit
from apps.loans.models import Loan, LoanRepayment, SavingEntry

from .engine import ZERO, period_sheets
from .models import DayAdjustment, LivingOutDays, PayPeriod, PayRules


class PeriodLocked(Exception):
    pass


def _open(period: PayPeriod):
    if period.is_locked:
        raise PeriodLocked("This pay period is approved. Reopen it to make changes.")


def _actor(user) -> str:
    return f"user:{user.get_username()}"


def save_adjustment(period, employee, date, normal, overtime, double, note, user):
    _open(period)
    adjustment, created = DayAdjustment.objects.update_or_create(
        employee=employee, date=date,
        defaults={"normal": normal, "overtime": overtime, "double": double, "note": note, "created_by": user})
    audit("timesheet.adjust", actor=_actor(user), company=period.company, target=f"{employee.employee_code} {date}",
          normal=str(normal), overtime=str(overtime), double=str(double), note=note)
    return adjustment


def clear_adjustment(period, employee, date, user):
    _open(period)
    deleted, _ = DayAdjustment.objects.filter(employee=employee, date=date).delete()
    if deleted:
        audit("timesheet.clear_adjustment", actor=_actor(user), company=period.company,
              target=f"{employee.employee_code} {date}")


def set_loa_nights(period, employee, nights: int | None, user):
    _open(period)
    if nights is None:
        LivingOutDays.objects.filter(period=period, employee=employee).delete()
    else:
        LivingOutDays.objects.update_or_create(period=period, employee=employee, defaults={"nights": nights})
    audit("timesheet.loa", actor=_actor(user), company=period.company, target=employee.employee_code,
          nights=nights if nights is not None else "calculated")


def set_loan_choice(period, loan, skipped: bool, amount: Decimal | None, note: str, user):
    _open(period)
    LoanRepayment.objects.update_or_create(
        loan=loan, period=period,
        defaults={"skipped": skipped, "amount": ZERO if skipped or amount is None else amount, "note": note})
    audit("loans.period_choice", actor=_actor(user), company=period.company, target=str(loan),
          skipped=skipped, amount=str(amount), note=note)


def set_saving_skip(period, saving, skipped: bool, user):
    _open(period)
    SavingEntry.objects.update_or_create(
        saving=saving, period=period, kind=SavingEntry.Kind.CONTRIBUTION,
        defaults={"skipped": skipped, "amount": ZERO if skipped else saving.amount_per_run})
    audit("savings.period_choice", actor=_actor(user), company=period.company, target=str(saving), skipped=skipped)


def approve(period: PayPeriod, user):
    """Lock the period and record what each loan and saving actually deducted."""
    _open(period)
    sheets = period_sheets(period)
    with transaction.atomic():
        for sheet in sheets:
            for due in sheet.loans:
                LoanRepayment.objects.update_or_create(
                    loan=due.loan, period=period,
                    defaults={"amount": due.due, "skipped": due.skipped, "note": due.note})
            for due in sheet.savings:
                SavingEntry.objects.update_or_create(
                    saving=due.saving, period=period, kind=SavingEntry.Kind.CONTRIBUTION,
                    defaults={"amount": due.due, "skipped": due.skipped})
        period.status = PayPeriod.Status.APPROVED
        period.approved_by = user
        period.approved_at = timezone.now()
        period.save(update_fields=["status", "approved_by", "approved_at"])
        for sheet in sheets:
            for due in sheet.loans:
                if due.loan.balance <= 0:
                    due.loan.status = Loan.Status.SETTLED
                    due.loan.save(update_fields=["status"])
    audit("timesheet.approve", actor=_actor(user), company=period.company, target=str(period),
          employees=len(sheets))
    return sheets


def reopen(period: PayPeriod, user):
    with transaction.atomic():
        period.status = PayPeriod.Status.OPEN
        period.approved_by = None
        period.approved_at = None
        period.save(update_fields=["status", "approved_by", "approved_at"])
        # Loans this period paid off become active again until it's approved once more.
        for rep in LoanRepayment.objects.filter(period=period, loan__status=Loan.Status.SETTLED).select_related("loan"):
            if rep.loan.balance > 0:
                rep.loan.status = Loan.Status.ACTIVE
                rep.loan.save(update_fields=["status"])
    audit("timesheet.reopen", actor=_actor(user), company=period.company, target=str(period))


# ---- export -----------------------------------------------------------------

SUMMARY_HEADINGS = ["Employee number", "Name", "NT", "T1/2", "DT", "LOA nights", "LOA (R)", "Loans (R)",
                    "Savings (R)", "Days worked", "Days to check"]


def summary_rows(sheets):
    for s in sheets:
        yield [s.employee.employee_code, f"{s.employee.first_name} {s.employee.last_name}", s.normal, s.overtime,
               s.double, s.loa_nights, s.loa_amount, s.loan_total, s.saving_total, s.days_worked, s.issues]


def export_csv(period: PayPeriod) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(SUMMARY_HEADINGS)
    for row in summary_rows(period_sheets(period)):
        writer.writerow([f"{v:.2f}" if isinstance(v, Decimal) else v for v in row])
    return out.getvalue()


def export_xlsx(period: PayPeriod) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    sheets = period_sheets(period)
    rules = PayRules.for_company(period.company)
    wb = Workbook()
    bold = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="E8EDF1")
    warn_fill = PatternFill("solid", fgColor="FFF4D6")

    ws = wb.active
    ws.title = "Hrs PaySpace"
    ws.append(SUMMARY_HEADINGS)
    for row in summary_rows(sheets):
        ws.append([float(v) if isinstance(v, Decimal) else v for v in row])
        if row[-1]:
            for cell in ws[ws.max_row]:
                cell.fill = warn_fill
    total_row = ws.max_row + 1
    ws.cell(total_row, 2, "Total").font = bold
    for col in range(3, 10):
        letter = get_column_letter(col)
        ws.cell(total_row, col, f"=SUM({letter}2:{letter}{total_row - 1})").font = bold
    for cell in ws[1]:
        cell.font, cell.fill = bold, head_fill
    ws.freeze_panes = "C2"
    for col, width in zip("ABCDEFGHIJK", [16, 28, 8, 8, 8, 11, 10, 10, 11, 12, 13], strict=True):
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=2, min_col=3, max_col=9):
        for cell in row:
            cell.number_format = "0.00"

    daily = wb.create_sheet("Daily hours")
    days = period.days()
    daily.append(["Employee number", "Name"] + [d.strftime("%a %d %b") for d in days for _ in range(3)]
                 + ["NT", "T1/2", "DT"])
    daily.append(["", ""] + ["NT", "T1/2", "DT"] * len(days) + ["", "", ""])
    for sheet in sheets:
        values = []
        for d in sheet.days:
            values += [float(d.normal) or None, float(d.overtime) or None, float(d.double) or None]
        daily.append([sheet.employee.employee_code, sheet.employee.full_name] + values
                     + [float(sheet.normal), float(sheet.overtime), float(sheet.double)])
        for i, d in enumerate(sheet.days):
            if d.issue:
                for k in range(3):
                    daily.cell(daily.max_row, 3 + i * 3 + k).fill = warn_fill
    for cell in list(daily[1]) + list(daily[2]):
        cell.font, cell.fill = bold, head_fill
        cell.alignment = Alignment(horizontal="center")
    daily.freeze_panes = "C3"
    daily.column_dimensions["B"].width = 26

    loans = wb.create_sheet("Loans and savings")
    loans.append(["Employee number", "Name", "Type", "Note", "Balance before (R)", "Deducted this run (R)", "Skipped"])
    for sheet in sheets:
        for due in sheet.loans:
            loans.append([sheet.employee.employee_code, sheet.employee.full_name, due.loan.get_kind_display(),
                          due.note or due.loan.note, float(due.balance_before), float(due.due),
                          "Yes" if due.skipped else ""])
        for due in sheet.savings:
            loans.append([sheet.employee.employee_code, sheet.employee.full_name, "Savings", due.saving.note, "",
                          float(due.due), "Yes" if due.skipped else ""])
    for cell in loans[1]:
        cell.font, cell.fill = bold, head_fill
    loans.column_dimensions["B"].width = 26
    loans.column_dimensions["D"].width = 40

    notes = wb.create_sheet("Rules used")
    for label, value in [
        ("Pay period", f"{period.name} ({period.start:%d %b %Y} to {period.end:%d %b %Y})"),
        ("Normal hours per weekday", float(rules.normal_hours_per_day)),
        ("Overtime starts after (minutes past normal day)", rules.overtime_after_minutes),
        ("Hours counted in steps of (minutes)", rules.rounding_minutes),
        ("Unpaid break per day (minutes)", rules.unpaid_break_minutes),
        ("Saturday", rules.get_saturday_display()),
        ("Sunday", "All hours at 2x (DT)"),
        ("Public holiday worked", "All hours at 2x (DT)"),
        ("Public holiday not worked (weekday)", f"{rules.public_holiday_paid_hours} hours NT"),
        ("Living out allowance per night (R)", float(rules.living_out_daily_rate)),
        ("Status", period.get_status_display()),
    ]:
        notes.append([label, value])
    notes.column_dimensions["A"].width = 46
    notes.column_dimensions["B"].width = 50

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
