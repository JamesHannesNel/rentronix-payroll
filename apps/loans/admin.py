from django.contrib import admin

from .models import Loan, LoanRepayment, Saving, SavingEntry


class LoanRepaymentInline(admin.TabularInline):
    model = LoanRepayment
    extra = 0


@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = ["employee", "kind", "amount", "instalment", "start_date", "status"]
    list_filter = ["company", "kind", "status"]
    search_fields = ["employee__employee_code", "employee__last_name", "note"]
    inlines = [LoanRepaymentInline]


class SavingEntryInline(admin.TabularInline):
    model = SavingEntry
    extra = 0


@admin.register(Saving)
class SavingAdmin(admin.ModelAdmin):
    list_display = ["employee", "amount_per_run", "start_date", "status"]
    list_filter = ["company", "status"]
    inlines = [SavingEntryInline]
