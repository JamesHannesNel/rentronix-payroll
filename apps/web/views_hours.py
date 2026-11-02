"""Screens for pay periods (timesheets), loans and savings, settings and API keys."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.models import ApiKey, Membership, audit
from apps.employees.models import Employee, Site
from apps.loans.models import Loan, Saving
from apps.timesheets import services
from apps.timesheets.engine import period_sheets, suggest_next_period
from apps.timesheets.models import PayPeriod, PayRules, PublicHoliday

from .views import company_required, pay_required


def _hours(raw: str) -> Decimal:
    value = Decimal((raw or "0").replace(",", ".").strip() or "0")
    if value < 0 or value > 24:
        raise InvalidOperation
    return value.quantize(Decimal("0.01"))


def _money(raw: str) -> Decimal:
    value = Decimal((raw or "").replace("R", "").replace(" ", "").replace(",", ".").strip())
    if value <= 0:
        raise InvalidOperation
    return value.quantize(Decimal("0.01"))


def _period(request, pk) -> PayPeriod:
    return get_object_or_404(PayPeriod, pk=pk, company=request.company)


def _name_for(start: dt.date, end: dt.date) -> str:
    if start.month == end.month:
        return f"Fortnight {start.day}–{end.day} {end:%b %Y}"
    return f"Fortnight {start.day} {start:%b}–{end.day} {end:%b %Y}"


# ---- pay periods --------------------------------------------------------------

@pay_required
def periods(request):
    return render(request, "web/periods.html", {
        "periods": PayPeriod.objects.filter(company=request.company),
        "suggested": suggest_next_period(request.company),
    })


@pay_required
@require_POST
def period_new(request):
    try:
        start = dt.date.fromisoformat(request.POST.get("start", ""))
        end = dt.date.fromisoformat(request.POST.get("end", ""))
    except ValueError:
        messages.error(request, "Choose a start and end date.")
        return redirect("web:periods")
    period = PayPeriod(company=request.company, start=start, end=end,
                       name=(request.POST.get("name") or _name_for(start, end))[:60])
    try:
        period.full_clean()
    except forms.ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("web:periods")
    overlap = PayPeriod.objects.filter(company=request.company, start__lte=end, end__gte=start).first()
    if overlap:
        messages.error(request, f"That overlaps {overlap.name}. Pick dates after {overlap.end:%d %b}.")
        return redirect("web:periods")
    period.save()
    audit("timesheet.create", actor=f"user:{request.user.get_username()}", company=request.company,
          target=str(period))
    return redirect("web:period", pk=period.pk)


@pay_required
def period(request, pk):
    period = _period(request, pk)
    sheets = period_sheets(period)
    show = request.GET.get("show", "all")
    if show == "issues":
        sheets_shown = [s for s in sheets if s.issues]
    elif show == "deductions":
        sheets_shown = [s for s in sheets if s.loans or s.savings]
    else:
        sheets_shown = sheets
    totals = {
        "normal": sum((s.normal for s in sheets), Decimal(0)),
        "overtime": sum((s.overtime for s in sheets), Decimal(0)),
        "double": sum((s.double for s in sheets), Decimal(0)),
        "loa": sum((s.loa_amount for s in sheets), Decimal(0)),
        "loans": sum((s.loan_total for s in sheets), Decimal(0)),
        "savings": sum((s.saving_total for s in sheets), Decimal(0)),
        "issues": sum(s.issues for s in sheets),
        "with_issues": sum(1 for s in sheets if s.issues),
        "with_deductions": sum(1 for s in sheets if s.loans or s.savings),
    }
    return render(request, "web/period.html", {
        "period": period, "sheets": sheets_shown, "all_count": len(sheets), "totals": totals, "show": show,
        "days": period.days(), "rules": PayRules.for_company(period.company),
    })


@pay_required
def period_employee(request, pk, employee_pk):
    period = _period(request, pk)
    employee = get_object_or_404(Employee, pk=employee_pk, company=request.company)
    sheets = period_sheets(period, employee_ids=[employee.pk])
    if not sheets:
        messages.error(request, f"{employee.full_name} isn't part of this pay period.")
        return redirect("web:period", pk=pk)
    return render(request, "web/period_employee.html", {
        "period": period, "sheet": sheets[0], "employee": employee,
        "rules": PayRules.for_company(period.company),
    })


@pay_required
@require_POST
def period_employee_save(request, pk, employee_pk):
    """One form on the person's page: day corrections, LOA nights, loan and saving choices."""
    period = _period(request, pk)
    employee = get_object_or_404(Employee, pk=employee_pk, company=request.company)
    user = request.user
    errors = []
    try:
        what = request.POST.get("what")
        if what == "day":
            day = dt.date.fromisoformat(request.POST["date"])
            if not (period.start <= day <= period.end):
                raise PermissionDenied
            if request.POST.get("clear"):
                services.clear_adjustment(period, employee, day, user)
                messages.success(request, f"{day:%a %d %b} is back to the hours from the clockings.")
            else:
                note = request.POST.get("note", "").strip()
                if not note:
                    errors.append("Add a short note saying why the hours were changed.")
                else:
                    try:
                        values = [_hours(request.POST.get(k)) for k in ("normal", "overtime", "double")]
                    except (InvalidOperation, ValueError):
                        errors.append("Hours must be numbers between 0 and 24.")
                    else:
                        services.save_adjustment(period, employee, day, *values, note, user)
                        messages.success(request, f"Saved the hours for {day:%a %d %b}.")
        elif what == "loa":
            raw = request.POST.get("nights", "").strip()
            if request.POST.get("reset") or raw == "":
                services.set_loa_nights(period, employee, None, user)
                messages.success(request, "Living out nights are back to the calculated number.")
            elif raw.isdigit() and int(raw) <= 31:
                services.set_loa_nights(period, employee, int(raw), user)
                messages.success(request, f"Living out allowance set to {raw} nights.")
            else:
                errors.append("Living out nights must be a whole number from 0 to 31.")
        elif what == "loan":
            loan = get_object_or_404(Loan, pk=request.POST.get("loan"), employee=employee)
            skipped = bool(request.POST.get("skip"))
            amount = None
            if not skipped and request.POST.get("amount", "").strip():
                try:
                    amount = _money(request.POST["amount"])
                except (InvalidOperation, ValueError):
                    errors.append("The deduction must be an amount above R0.")
            if not errors:
                services.set_loan_choice(period, loan, skipped, amount, request.POST.get("note", "").strip(), user)
                messages.success(request, "Skipped for this pay run." if skipped else "Deduction updated.")
        elif what == "saving":
            saving = get_object_or_404(Saving, pk=request.POST.get("saving"), employee=employee)
            services.set_saving_skip(period, saving, bool(request.POST.get("skip")), user)
            messages.success(request, "Saving updated for this pay run.")
    except services.PeriodLocked as exc:
        errors.append(str(exc))
    for error in errors:
        messages.error(request, error)
    return redirect("web:period_employee", pk=pk, employee_pk=employee_pk)


@pay_required
@require_POST
def period_action(request, pk, action):
    period = _period(request, pk)
    if action == "approve":
        try:
            services.approve(period, request.user)
            messages.success(request, "Approved. Loans and savings are updated. Export the hours for PaySpace.")
        except services.PeriodLocked as exc:
            messages.error(request, str(exc))
    elif action == "reopen":
        services.reopen(period, request.user)
        messages.warning(request, "Reopened. Approve it again once your changes are done.")
    elif action in ("excel", "csv"):
        stamp = f"{period.start:%Y%m%d}-{period.end:%Y%m%d}"
        audit("timesheet.export", actor=f"user:{request.user.get_username()}", company=period.company,
              target=str(period), format=action)
        if action == "excel":
            response = HttpResponse(services.export_xlsx(period), content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
            response["Content-Disposition"] = f'attachment; filename="Hours-for-PaySpace-{stamp}.xlsx"'
        else:
            response = HttpResponse(services.export_csv(period), content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = f'attachment; filename="Hours-for-PaySpace-{stamp}.csv"'
        return response
    else:
        raise PermissionDenied
    return redirect("web:period", pk=pk)


# ---- loans and savings ----------------------------------------------------------

class LoanForm(forms.ModelForm):
    class Meta:
        model = Loan
        fields = ["employee", "kind", "amount", "instalment", "start_date", "note"]
        labels = {"kind": "Type", "amount": "Amount to repay (R)", "instalment": "Deduct each pay run (R)",
                  "start_date": "Start deducting from"}
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = Employee.objects.filter(company=company, status=Employee.Status.ACTIVE)

    def clean(self):
        data = super().clean()
        if data.get("instalment") and data.get("amount") and data["instalment"] > data["amount"]:
            self.add_error("instalment", "The deduction per pay run can't be more than the amount owed.")
        return data


class SavingForm(forms.ModelForm):
    class Meta:
        model = Saving
        fields = ["employee", "amount_per_run", "start_date", "note"]
        labels = {"amount_per_run": "Save each pay run (R)", "start_date": "Start saving from"}
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = Employee.objects.filter(company=company, status=Employee.Status.ACTIVE)


@pay_required
def loans(request):
    company = request.company
    status = request.GET.get("status", "active")
    loan_qs = Loan.objects.filter(company=company).select_related("employee").prefetch_related("repayments__period")
    if status == "active":
        loan_qs = loan_qs.filter(status=Loan.Status.ACTIVE)
    loan_form = LoanForm(company=company, initial={"start_date": timezone.localdate()}, prefix="loan")
    saving_form = SavingForm(company=company, initial={"start_date": timezone.localdate()}, prefix="saving")
    if request.method == "POST":
        if "loan-employee" in request.POST:
            loan_form = LoanForm(request.POST, company=company, prefix="loan")
            if loan_form.is_valid():
                loan = loan_form.save(commit=False)
                loan.company, loan.created_by = company, request.user
                loan.save()
                audit("loans.create", actor=f"user:{request.user.get_username()}", company=company, target=str(loan))
                messages.success(request, f"Added: {loan}. It's deducted from the next open pay period.")
                return redirect("web:loans")
        else:
            saving_form = SavingForm(request.POST, company=company, prefix="saving")
            if saving_form.is_valid():
                saving = saving_form.save(commit=False)
                saving.company = company
                saving.save()
                audit("savings.create", actor=f"user:{request.user.get_username()}", company=company,
                      target=str(saving))
                messages.success(request, f"Added: {saving}.")
                return redirect("web:loans")
    savings = Saving.objects.filter(company=company).select_related("employee")
    if status == "active":
        savings = savings.filter(status=Saving.Status.ACTIVE)
    return render(request, "web/loans.html", {
        "loans": loan_qs, "savings": savings, "loan_form": loan_form, "saving_form": saving_form, "status": status,
        "show_form": request.GET.get("add") or request.method == "POST",
    })


@pay_required
@require_POST
def loan_action(request, pk, action):
    loan = get_object_or_404(Loan, pk=pk, company=request.company)
    if action == "cancel":
        loan.status = Loan.Status.CANCELLED
    elif action == "settle":
        loan.status = Loan.Status.SETTLED
    elif action == "resume":
        loan.status = Loan.Status.ACTIVE
    else:
        raise PermissionDenied
    loan.save(update_fields=["status"])
    audit(f"loans.{action}", actor=f"user:{request.user.get_username()}", company=loan.company, target=str(loan))
    messages.success(request, f"{loan.get_kind_display()} for {loan.employee.full_name}: {loan.get_status_display()}.")
    return redirect("web:loans")


@pay_required
@require_POST
def saving_action(request, pk, action):
    saving = get_object_or_404(Saving, pk=pk, company=request.company)
    if action not in ("stop", "resume"):
        raise PermissionDenied
    saving.status = Saving.Status.STOPPED if action == "stop" else Saving.Status.ACTIVE
    saving.save(update_fields=["status"])
    audit(f"savings.{action}", actor=f"user:{request.user.get_username()}", company=saving.company,
          target=str(saving))
    return redirect("web:loans")


# ---- settings -----------------------------------------------------------------

def owner_required(view):
    @company_required
    def wrapper(request, *args, **kwargs):
        user = request.user
        if not (user.is_superuser or Membership.objects.filter(
                user=user, company=request.company, role=Membership.Role.OWNER).exists()):
            raise PermissionDenied("Only owners can change settings.")
        return view(request, *args, **kwargs)

    wrapper.__name__ = view.__name__
    return wrapper


class RulesForm(forms.ModelForm):
    class Meta:
        model = PayRules
        fields = ["normal_hours_per_day", "overtime_after_minutes", "rounding_minutes", "unpaid_break_minutes",
                  "saturday", "public_holiday_paid_hours", "living_out_daily_rate"]
        labels = {
            "normal_hours_per_day": "Normal hours per weekday",
            "overtime_after_minutes": "Overtime starts after (minutes)",
            "rounding_minutes": "Count hours in steps of (minutes)",
            "unpaid_break_minutes": "Unpaid break per day (minutes)",
            "public_holiday_paid_hours": "Hours paid on a public holiday not worked",
            "living_out_daily_rate": "Living out allowance per night (R)",
        }


@owner_required
def settings_page(request):
    company = request.company
    rules = PayRules.for_company(company)
    form = RulesForm(request.POST or None, instance=rules)
    if request.method == "POST" and request.POST.get("what") == "rules":
        if form.is_valid():
            form.save()
            audit("settings.rules", actor=f"user:{request.user.get_username()}", company=company, target="pay rules",
                  **{k: str(v) for k, v in form.cleaned_data.items()})
            messages.success(request, "Pay rules saved. Open pay periods use them straight away.")
            return redirect("web:settings")
    if request.method == "POST" and request.POST.get("what") == "sites":
        chosen = set(request.POST.getlist("living_out"))
        for site in Site.objects.filter(company=company):
            flag = str(site.pk) in chosen
            if site.living_out != flag:
                site.living_out = flag
                site.save(update_fields=["living_out"])
        messages.success(request, "Sleep-over sites saved.")
        return redirect("web:settings")
    today = timezone.localdate()
    return render(request, "web/settings.html", {
        "form": form, "sites": Site.objects.filter(company=company),
        "holidays": PublicHoliday.objects.filter(date__gte=today.replace(month=1, day=1)),
        "next_year_missing": not PublicHoliday.objects.filter(date__year=today.year + 1).exists(),
    })


@owner_required
def api_keys(request):
    company = request.company
    new_key = None
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            name = request.POST.get("name", "").strip()[:100]
            scopes = [s for s in request.POST.getlist("scopes") if s in ApiKey.Scope.values]
            if not name or not scopes:
                messages.error(request, "Give the key a name and choose at least one permission.")
            else:
                key, new_key = ApiKey.issue(company, name, scopes)
                audit("api_key.issue", actor=f"user:{request.user.get_username()}", company=company, target=str(key))
        elif action in ("disable", "enable"):
            key = get_object_or_404(ApiKey, pk=request.POST.get("key"), company=company)
            key.is_active = action == "enable"
            key.save(update_fields=["is_active"])
            audit(f"api_key.{action}", actor=f"user:{request.user.get_username()}", company=company, target=str(key))
            messages.success(request, f"{key.name} {'enabled' if key.is_active else 'disabled'}.")
            return redirect("web:api_keys")
    return render(request, "web/api_keys.html", {
        "keys": ApiKey.objects.filter(company=company), "new_key": new_key, "scopes": ApiKey.Scope.choices,
        "api_base": request.build_absolute_uri("/api/v1/"),
    })
