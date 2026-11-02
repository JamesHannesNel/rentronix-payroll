"""The everyday screens: dashboard, attendance, employees, pay runs, readers.

Setup (companies, users, API keys, PaySpace links) stays in /admin/.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.models import audit
from apps.employees.models import Employee, Site
from apps.payments import fnb, services
from apps.payments.models import CompanyBankAccount, PaymentBatch
from apps.payspace.models import SyncRun
from apps.timeclock.attendance import attendance_for_day, unmatched_count
from apps.timeclock.models import ClockRecord, Device

from .context import SESSION_KEY, can_pay, companies_for, current_company

READER_OFFLINE_AFTER = dt.timedelta(hours=2)


def company_required(view):
    """Signed in, and looking at a company they're allowed to see."""

    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        company = current_company(request)
        if company is None:
            return render(request, "web/no_company.html", status=403)
        request.company = company
        return view(request, *args, **kwargs)

    return wrapper


def pay_required(view):
    @company_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not can_pay(request.user, request.company):
            raise PermissionDenied("Only owners and payroll administrators can manage payments.")
        return view(request, *args, **kwargs)

    return wrapper


def _parse_day(raw: str | None) -> dt.date:
    try:
        return dt.date.fromisoformat(raw) if raw else timezone.localdate()
    except ValueError:
        return timezone.localdate()


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _actor(request) -> str:
    return f"user:{request.user.get_username()}"


@login_required
@require_POST
def switch_company(request):
    company = companies_for(request.user).filter(pk=request.POST.get("company")).first()
    if company is not None:
        request.session[SESSION_KEY] = company.pk
    return redirect(request.POST.get("next") or "web:dashboard")


# ---- dashboard --------------------------------------------------------------

@company_required
def dashboard(request):
    company, today = request.company, timezone.localdate()
    lines = attendance_for_day(company, today)

    boards: dict[str, dict] = {}
    for line in lines:
        name = line.employee.site.name if line.employee.site else "No site"
        board = boards.setdefault(name, {"name": name, "site_id": line.employee.site_id, "lines": [], "present": 0})
        board["lines"].append(line)
        board["present"] += line.present

    offline_cutoff = timezone.now() - READER_OFFLINE_AFTER
    readers = Device.objects.filter(company=company)
    offline = readers.filter(Q(is_online=False) | Q(last_seen_at__lt=offline_cutoff) | Q(last_seen_at__isnull=True))

    attention = []
    # The last 7 days, oldest first: who was in, and days with a missing clock.
    week = []
    for back in range(6, -1, -1):
        day = today - dt.timedelta(days=back)
        day_lines = lines if back == 0 else attendance_for_day(company, day)
        week.append({
            "day": day,
            "present": sum(line.present for line in day_lines),
            "missing_in": sum(line.status == "missing_in" for line in day_lines),
            "missing_out": sum(line.status == "missing_out" for line in day_lines),
            "headcount": len(day_lines),
        })
    peak = max([w["present"] for w in week] + [1])
    for w in week:
        w["bar"] = round(100 * w["present"] / peak)
    incomplete_days = [w for w in week if w["missing_in"] or w["missing_out"]]
    if incomplete_days:
        count = sum(w["missing_in"] + w["missing_out"] for w in incomplete_days)
        attention.append({"text": f"{plural(count, 'missing clocking', 'missing clockings')} in the last 7 days",
                          "url": f"/attendance/?date={incomplete_days[-1]['day']}&show=attention"})
    unknown = unmatched_count(company, today)
    if unknown:
        attention.append({"text": f"{plural(unknown, 'clocking', 'clockings')} today from an unknown employee number",
                          "url": "/attendance/unknown/"})
    no_bank = Employee.objects.filter(company=company, status=Employee.Status.ACTIVE, bank_account__isnull=True)
    if no_bank.exists():
        attention.append({"text": f"{plural(no_bank.count(), 'active employee', 'active employees')} without bank details",
                          "url": "/employees/?missing=bank"})

    # Hours so far in the current pay period.
    from apps.timesheets.engine import period_sheets
    from apps.timesheets.models import PayPeriod

    period = PayPeriod.objects.filter(company=company, start__lte=today, end__gte=today).first()
    period_totals = None
    if period:
        sheets = period_sheets(period)
        saturday = holiday_paid = Decimal(0)
        for sheet in sheets:
            for d in sheet.days:
                if d.kind == "saturday":
                    saturday += d.total
                elif d.kind == "holiday":
                    holiday_paid += d.normal
        period_totals = {
            "normal": sum((s.normal for s in sheets), Decimal(0)),
            "overtime": sum((s.overtime for s in sheets), Decimal(0)),
            "double": sum((s.double for s in sheets), Decimal(0)),
            "saturday": saturday, "holiday_paid": holiday_paid,
        }

    open_batches = PaymentBatch.objects.filter(company=company).exclude(status=PaymentBatch.Status.EXPORTED)
    last_sync = SyncRun.objects.filter(company=company).first()
    present = sum(line.present for line in lines)
    devices_total = readers.count()
    return render(request, "web/dashboard.html", {
        "today": today, "boards": list(boards.values()), "present": present, "headcount": len(lines),
        "absent": len(lines) - present, "on_site_now": sum(line.status == "on_site" for line in lines),
        "devices_online": devices_total - offline.count(), "devices_total": devices_total,
        "week": week, "period": period, "period_totals": period_totals,
        "attention": attention, "open_batches": open_batches[:3], "last_sync": last_sync,
        "recent": ClockRecord.objects.filter(company=company).select_related("employee").order_by("-clocked_at")[:8],
    })


# ---- attendance -------------------------------------------------------------

@company_required
def attendance(request):
    company = request.company
    day = _parse_day(request.GET.get("date"))
    site = Site.objects.filter(company=company, pk=request.GET.get("site") or None).first()
    lines = attendance_for_day(company, day, site=site)
    show = request.GET.get("show", "all")
    counts = {
        "all": len(lines),
        "present": sum(line.present for line in lines),
        "absent": sum(not line.present for line in lines),
        "attention": sum(line.needs_attention for line in lines),
    }
    if show == "present":
        lines = [line for line in lines if line.present]
    elif show == "absent":
        lines = [line for line in lines if not line.present]
    elif show == "attention":
        lines = [line for line in lines if line.needs_attention]
    return render(request, "web/attendance.html", {
        "day": day, "prev": day - dt.timedelta(days=1), "next": day + dt.timedelta(days=1),
        "is_today": day == timezone.localdate(), "lines": lines, "counts": counts, "show": show,
        "site": site, "sites": Site.objects.filter(company=company),
        "total_hours": sum((line.hours for line in lines), Decimal("0")),
        "unknown": unmatched_count(company, day),
    })


@company_required
def unknown_clocks(request):
    clocks = (ClockRecord.objects.filter(company=request.company, employee__isnull=True)
              .order_by("-clocked_at")[:200])
    return render(request, "web/unknown_clocks.html", {"clocks": clocks})


# ---- employees --------------------------------------------------------------

@company_required
def employees(request):
    company = request.company
    qs = Employee.objects.filter(company=company).select_related("site", "bank_account")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(employee_code__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
                       | Q(preferred_name__icontains=q) | Q(job_title__icontains=q))
    status = request.GET.get("status", "active")
    if status in Employee.Status.values:
        qs = qs.filter(status=status)
    site = Site.objects.filter(company=company, pk=request.GET.get("site") or None).first()
    if site:
        qs = qs.filter(site=site)
    if request.GET.get("missing") == "bank":
        qs = qs.filter(bank_account__isnull=True)
    return render(request, "web/employees.html", {
        "employees": qs.order_by("last_name", "first_name"), "q": q, "status": status, "site": site,
        "sites": Site.objects.filter(company=company).annotate(n=Count("employees")),
        "missing": request.GET.get("missing", ""),
    })


@company_required
def employees_export(request):
    """Excel list of employees, filtered the same way as the screen."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    company = request.company
    qs = Employee.objects.filter(company=company).select_related("site", "team", "bank_account")
    status = request.GET.get("status", "active")
    if status in Employee.Status.values:
        qs = qs.filter(status=status)
    site = Site.objects.filter(company=company, pk=request.GET.get("site") or None).first()
    if site:
        qs = qs.filter(site=site)
    wb = Workbook()
    ws = wb.active
    ws.title = "Employees"
    ws.append(["Employee number", "First name", "Last name", "Known as", "Job", "Site", "Team", "Pay frequency",
               "Started", "Status", "Cellphone", "Email", "Biometrics enrolled", "Bank details captured"])
    for e in qs.order_by("last_name", "first_name"):
        ws.append([e.employee_code, e.first_name, e.last_name, e.preferred_name, e.job_title,
                   e.site.name if e.site else "", e.team.name if e.team else "", e.pay_frequency, e.date_engaged,
                   e.get_status_display(), e.cell_number, e.email, "Yes" if e.biometric_enrolled else "No",
                   "Yes" if hasattr(e, "bank_account") else "No"])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for col, width in zip("ABCDEFGHIJKLMN", [16, 16, 18, 12, 20, 18, 14, 13, 12, 11, 14, 24, 10, 12], strict=True):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "B2"
    import io

    buffer = io.BytesIO()
    wb.save(buffer)
    audit("employees.export", actor=_actor(request), company=company, target="employees", count=ws.max_row - 1)
    response = HttpResponse(buffer.getvalue(),
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="Employees-{timezone.localdate():%Y%m%d}.xlsx"'
    return response


@company_required
def employee_detail(request, pk):
    employee = get_object_or_404(Employee.objects.select_related("site", "team"), pk=pk, company=request.company)
    today = timezone.localdate()
    from apps.timeclock.attendance import build_line, day_bounds

    days = []
    for offset in range(14):
        day = today - dt.timedelta(days=offset)
        start, end = day_bounds(day)
        clocks = list(employee.clocks.filter(clocked_at__gte=start, clocked_at__lt=end).order_by("clocked_at", "id"))
        days.append((day, build_line(employee, clocks, day, timezone.now())))
    bank = getattr(employee, "bank_account", None)
    return render(request, "web/employee_detail.html", {
        "employee": employee, "days": days, "bank": bank,
        "fortnight_hours": sum((line.hours for _, line in days), Decimal("0")),
        "can_edit": request.user.is_staff,
    })


# ---- pay runs ---------------------------------------------------------------

@pay_required
def pay_runs(request):
    batches = PaymentBatch.objects.filter(company=request.company).prefetch_related("lines")
    return render(request, "web/pay_runs.html", {"batches": batches})


@pay_required
def pay_run_new(request):
    company = request.company
    accounts = CompanyBankAccount.objects.filter(company=company)
    if request.method == "POST":
        account = accounts.filter(pk=request.POST.get("paying_account")).first()
        name = request.POST.get("name", "").strip()
        try:
            action_date = dt.date.fromisoformat(request.POST.get("action_date", ""))
        except ValueError:
            action_date = None
        if account and name and action_date:
            batch = PaymentBatch.objects.create(
                company=company, paying_account=account, name=name[:60], action_date=action_date,
                own_reference=(request.POST.get("own_reference") or "SALARIES")[:15], created_by=request.user)
            audit("payments.create", actor=_actor(request), company=company, target=str(batch))
            added, missing = services.add_active_employees(batch, _actor(request))
            messages.success(request, f"Pay run created with {added} employees. Enter or import their net pay.")
            if missing:
                messages.warning(request, f"Left out, no bank details: {', '.join(missing)}")
            return redirect("web:pay_run", pk=batch.pk)
        messages.error(request, "Choose the paying account, a name and a payment date.")
    return render(request, "web/pay_run_new.html", {
        "accounts": accounts, "suggested_date": timezone.localdate() + dt.timedelta(days=1)})


def _batch(request, pk) -> PaymentBatch:
    return get_object_or_404(PaymentBatch.objects.select_related("paying_account"), pk=pk, company=request.company)


@pay_required
def pay_run(request, pk):
    batch = _batch(request, pk)
    lines = list(batch.lines.select_related("employee").order_by("employee__last_name", "employee__first_name"))
    problems = fnb.check(batch)
    by_code = {}
    for problem in problems:
        by_code.setdefault(problem.employee_code, []).append(problem.message)
    general = by_code.pop("", [])
    zero = sum(1 for line in lines if line.amount <= 0)
    for line in lines:
        # A R0.00 line is "not being paid", not a problem: it's removed before approval.
        line.problems = [] if line.amount <= 0 else by_code.get(line.employee.employee_code, [])
    problems = [p for p in problems if not p.employee_code or any(
        line.problems for line in lines if line.employee.employee_code == p.employee_code)]
    if batch.status == PaymentBatch.Status.DRAFT:
        step = 3 if not problems else (2 if zero else 3)
    elif batch.status == PaymentBatch.Status.APPROVED:
        step = 4
    else:
        step = 5
    return render(request, "web/pay_run.html", {
        "batch": batch, "lines": lines, "problems": problems, "line_problems": by_code, "general": general,
        "zero": zero, "step": step, "total": batch.total, "paid_count": len(lines) - zero,
    })


@pay_required
@require_POST
def pay_run_amounts(request, pk):
    batch = _batch(request, pk)
    if batch.is_locked:
        messages.error(request, "This pay run is approved. Reopen it to change amounts.")
        return redirect("web:pay_run", pk=pk)
    changed, bad = [], []
    for line in batch.lines.select_related("employee"):
        raw = request.POST.get(f"amount_{line.pk}")
        if raw is None:
            continue
        try:
            amount = services._parse_amount(raw) if raw.strip() else Decimal("0.00")
        except (InvalidOperation, ValueError):
            bad.append(line.employee.employee_code)
            continue
        if amount != line.amount:
            line.amount = amount
            changed.append(line)
    if bad:
        messages.error(request, f"Not saved. Check the amounts for: {', '.join(bad)}")
        return redirect("web:pay_run", pk=pk)
    for line in changed:
        line.save(update_fields=["amount"])
    if changed:
        audit("payments.edit_amounts", actor=_actor(request), company=batch.company, target=str(batch),
              changed=len(changed))
    messages.success(request, f"Saved {plural(len(changed), 'amount', 'amounts')}.")
    return redirect("web:pay_run", pk=pk)


@pay_required
@require_POST
def pay_run_import(request, pk):
    batch = _batch(request, pk)
    upload = request.FILES.get("file")
    if not upload:
        messages.error(request, "Choose a CSV file to import.")
        return redirect("web:pay_run", pk=pk)
    raw = upload.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    try:
        result = services.import_amounts(batch, text, _actor(request))
    except services.BatchError as exc:
        messages.error(request, str(exc))
        return redirect("web:pay_run", pk=pk)
    if result.errors:
        messages.error(request, "Nothing imported. " + " ".join(result.errors[:10]))
    else:
        messages.success(request, f"Imported net pay: {result.updated} updated, {result.added} added.")
    return redirect("web:pay_run", pk=pk)


@pay_required
@require_POST
def pay_run_action(request, pk, action):
    batch = _batch(request, pk)
    try:
        if action == "remove-zero":
            removed = services.remove_zero_lines(batch, _actor(request))
            messages.success(request, f"Left out {plural(removed, 'worker', 'workers')} with no pay.")
        elif action == "approve":
            problems = services.approve(batch, request.user)
            if problems:
                messages.error(request, "Not approved yet. Fix the items marked below.")
            else:
                messages.success(request, "Approved. Download the FNB file and upload it in FNB Online Banking.")
        elif action == "reopen":
            services.reopen(batch, request.user)
            messages.warning(request, "Reopened for changes. If you already uploaded the old file to FNB, "
                                      "delete that batch in FNB before uploading the new one.")
        elif action == "download":
            name, text = services.export(batch, request.user)
            response = HttpResponse(text, content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = f'attachment; filename="{name}"'
            return response
        else:
            raise PermissionDenied
    except services.BatchError as exc:
        messages.error(request, str(exc))
    return redirect("web:pay_run", pk=pk)


# ---- readers ----------------------------------------------------------------

@company_required
def readers(request):
    cutoff = timezone.now() - READER_OFFLINE_AFTER
    devices = list(Device.objects.filter(company=request.company).select_related("site").annotate(
        clocks_today=Count("clocks", filter=Q(clocks__clocked_at__date=timezone.localdate()))))
    for d in devices:
        d.online_now = bool(d.is_online and d.last_seen_at and d.last_seen_at >= cutoff)
    devices.sort(key=lambda d: (d.online_now, d.name or d.serial))  # offline readers first
    return render(request, "web/readers.html", {"devices": devices, "cutoff": cutoff})
