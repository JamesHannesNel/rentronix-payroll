from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.html import format_html

from apps.employees.admin import EmployeeAdmin
from apps.employees.models import Employee

from . import fnb, services
from .models import CompanyBankAccount, EmployeeBankAccount, PaymentBatch, PaymentLine


def masked(number: str) -> str:
    return f"••••{number[-4:]}" if number else ""


# ---- employee bank details, shown on the employee page -------------------

class EmployeeBankAccountInline(admin.StackedInline):
    model = EmployeeBankAccount
    verbose_name = verbose_name_plural = "Bank details"
    extra = 0
    max_num = 1
    can_delete = True
    fields = ["bank", "branch_code", "account_number", "account_type", "account_holder"]


admin.site.unregister(Employee)


@admin.register(Employee)
class EmployeeWithBankAdmin(EmployeeAdmin):
    inlines = [EmployeeBankAccountInline]
    list_display = [*EmployeeAdmin.list_display, "has_bank_details"]

    @admin.display(boolean=True, description="Bank details")
    def has_bank_details(self, obj):
        return hasattr(obj, "bank_account")


@admin.register(CompanyBankAccount)
class CompanyBankAccountAdmin(admin.ModelAdmin):
    list_display = ["name", "company", "masked_number", "is_default"]
    list_filter = ["company"]

    @admin.display(description="Account")
    def masked_number(self, obj):
        return masked(obj.account_number)


# ---- payment batches ------------------------------------------------------

class PaymentLineInline(admin.TabularInline):
    model = PaymentLine
    extra = 0
    fields = ["employee", "recipient_name", "bank_account", "account_type", "amount", "note"]
    readonly_fields = ["employee", "recipient_name", "bank_account", "account_type"]

    @admin.display(description="Account")
    def bank_account(self, obj):
        return f"{obj.branch_code} / {masked(obj.account_number)}"

    def has_add_permission(self, request, obj=None):
        return False  # lines come from "Add active employees" or "Import amounts"

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.is_locked:
            return [*self.readonly_fields, "amount", "note"]
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        return not (obj and obj.is_locked)


class AmountsUploadForm(forms.Form):
    file = forms.FileField(help_text="CSV with two columns: employee code, net pay. A heading row is fine.")


@admin.register(PaymentBatch)
class PaymentBatchAdmin(admin.ModelAdmin):
    list_display = ["name", "company", "action_date", "line_count", "batch_total", "status", "exported_at"]
    list_filter = ["company", "status"]
    readonly_fields = ["status", "summary", "created_by", "approved_by", "approved_at", "exported_at"]
    fields = ["company", "paying_account", "name", "action_date", "own_reference", "recipient_reference",
              "summary", "status", "created_by", "approved_by", "approved_at", "exported_at"]
    inlines = [PaymentLineInline]
    change_form_template = "admin/payments/paymentbatch/change_form.html"

    @admin.display(description="Payments")
    def line_count(self, obj):
        return obj.lines.count()

    @admin.display(description="Total (R)")
    def batch_total(self, obj):
        return f"{obj.total:,.2f}".replace(",", " ")

    @admin.display(description="Check")
    def summary(self, obj):
        if not obj.pk:
            return "Save the batch first, then add employees or import amounts."
        problems = fnb.check(obj)
        head = f"{obj.lines.count()} payments, total R {self.batch_total(obj)}. "
        if not problems:
            return format_html("{}<strong>Ready for FNB.</strong>", head)
        items = format_html("".join("<li>{}</li>" for _ in problems),
                            *[f"{p.employee_code}: {p.message}" if p.employee_code else p.message for p in problems])
        return format_html("{}<strong>{} to fix:</strong><ul>{}</ul>", head, len(problems), items)

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.is_locked:
            return [*self.readonly_fields, "company", "paying_account", "name", "action_date", "own_reference",
                    "recipient_reference"]
        return self.readonly_fields

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def get_urls(self):
        own = [
            path("<int:pk>/add-employees/", self.admin_site.admin_view(self.add_employees_view),
                 name="payments_batch_add_employees"),
            path("<int:pk>/import-amounts/", self.admin_site.admin_view(self.import_amounts_view),
                 name="payments_batch_import_amounts"),
            path("<int:pk>/remove-zero/", self.admin_site.admin_view(self.remove_zero_view),
                 name="payments_batch_remove_zero"),
            path("<int:pk>/approve/", self.admin_site.admin_view(self.approve_view), name="payments_batch_approve"),
            path("<int:pk>/reopen/", self.admin_site.admin_view(self.reopen_view), name="payments_batch_reopen"),
            path("<int:pk>/fnb-file/", self.admin_site.admin_view(self.fnb_file_view), name="payments_batch_fnb_file"),
        ]
        return own + super().get_urls()

    # Each action posts back to the batch page.
    def _back(self, batch):
        return HttpResponseRedirect(reverse("admin:payments_paymentbatch_change", args=[batch.pk]))

    def _batch(self, request, pk):
        batch = get_object_or_404(PaymentBatch, pk=pk)
        if not self.has_change_permission(request, batch):
            raise PermissionDenied
        return batch

    def _actor(self, request):
        return f"user:{request.user.get_username()}"

    def add_employees_view(self, request, pk):
        batch = self._batch(request, pk)
        if request.method == "POST":
            try:
                added, missing = services.add_active_employees(batch, self._actor(request))
            except services.BatchError as exc:
                self.message_user(request, str(exc), messages.ERROR)
            else:
                self.message_user(request, f"Added {added} employees at R0.00. Type amounts or import them.",
                                  messages.SUCCESS)
                if missing:
                    self.message_user(request, f"No bank details for: {', '.join(missing)}", messages.WARNING)
        return self._back(batch)

    def import_amounts_view(self, request, pk):
        batch = self._batch(request, pk)
        form = AmountsUploadForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            raw = form.cleaned_data["file"].read()
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("cp1252")
            try:
                result = services.import_amounts(batch, text, self._actor(request))
            except services.BatchError as exc:
                self.message_user(request, str(exc), messages.ERROR)
                return self._back(batch)
            if result.errors:
                for error in result.errors[:20]:
                    self.message_user(request, error, messages.ERROR)
                self.message_user(request, "Nothing was imported. Fix the file and try again.", messages.ERROR)
            else:
                self.message_user(request, f"Updated {result.updated} amounts, added {result.added} employees.",
                                  messages.SUCCESS)
                return self._back(batch)
        context = {**self.admin_site.each_context(request), "batch": batch, "form": form, "opts": self.model._meta,
                   "title": f"Import amounts into {batch.name}"}
        return render(request, "admin/payments/paymentbatch/import_amounts.html", context)

    def remove_zero_view(self, request, pk):
        batch = self._batch(request, pk)
        if request.method == "POST":
            try:
                removed = services.remove_zero_lines(batch, self._actor(request))
                self.message_user(request, f"Removed {removed} R0.00 lines.", messages.SUCCESS)
            except services.BatchError as exc:
                self.message_user(request, str(exc), messages.ERROR)
        return self._back(batch)

    def approve_view(self, request, pk):
        batch = self._batch(request, pk)
        if request.method == "POST":
            try:
                problems = services.approve(batch, request.user)
            except services.BatchError as exc:
                self.message_user(request, str(exc), messages.ERROR)
            else:
                if problems:
                    self.message_user(request, f"Not approved: {len(problems)} problem(s) listed under Check.",
                                      messages.ERROR)
                else:
                    self.message_user(request, "Approved and locked. You can now download the FNB file.",
                                      messages.SUCCESS)
        return self._back(batch)

    def reopen_view(self, request, pk):
        batch = self._batch(request, pk)
        if request.method == "POST":
            services.reopen(batch, request.user)
            self.message_user(request, "Reopened. If you already uploaded the old file to FNB, delete that batch "
                              "in FNB before uploading a new one.", messages.WARNING)
        return self._back(batch)

    def fnb_file_view(self, request, pk):
        batch = self._batch(request, pk)
        if request.method != "POST":
            return self._back(batch)
        try:
            name, text = services.export(batch, request.user)
        except services.BatchError as exc:
            self.message_user(request, str(exc), messages.ERROR)
            return self._back(batch)
        response = HttpResponse(text, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        return response
