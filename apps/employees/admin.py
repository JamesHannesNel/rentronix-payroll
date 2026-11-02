from django.contrib import admin

from .models import Employee, Site, Team


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ["name", "company", "living_out"]
    list_filter = ["company"]
    search_fields = ["name"]


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ["name", "company"]
    list_filter = ["company"]
    search_fields = ["name"]


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = [
        "employee_code", "full_name", "company", "site", "team", "pay_frequency",
        "status", "biometric_enrolled", "source",
    ]
    list_filter = ["company", "status", "source", "biometric_enrolled", "site"]
    search_fields = ["employee_code", "first_name", "last_name", "preferred_name"]
    readonly_fields = ["source", "payspace_employee_id", "biometric_checked_at", "created_at", "updated_at"]
    fieldsets = [
        (None, {"fields": ["company", "employee_code", "first_name", "last_name", "preferred_name", "status"]}),
        ("Contact", {"fields": ["email", "cell_number"]}),
        ("Work", {"fields": ["job_title", "pay_frequency", "site", "team", "date_engaged", "termination_date"]}),
        ("Biometrics", {"fields": ["biometric_enrolled", "biometric_method", "biometric_checked_at"]}),
        ("Sync", {"fields": ["source", "payspace_employee_id", "created_at", "updated_at"]}),
    ]
