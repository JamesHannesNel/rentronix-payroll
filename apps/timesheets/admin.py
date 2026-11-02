from django.contrib import admin

from .models import DayAdjustment, PayPeriod, PayRules, PublicHoliday


@admin.register(PublicHoliday)
class PublicHolidayAdmin(admin.ModelAdmin):
    list_display = ["date", "name"]
    date_hierarchy = "date"


@admin.register(PayRules)
class PayRulesAdmin(admin.ModelAdmin):
    list_display = ["company", "normal_hours_per_day", "overtime_after_minutes", "saturday", "living_out_daily_rate"]


@admin.register(PayPeriod)
class PayPeriodAdmin(admin.ModelAdmin):
    list_display = ["name", "company", "start", "end", "status"]
    list_filter = ["company", "status"]


@admin.register(DayAdjustment)
class DayAdjustmentAdmin(admin.ModelAdmin):
    list_display = ["employee", "date", "normal", "overtime", "double", "note", "created_by"]
    list_filter = ["employee__company"]
    search_fields = ["employee__employee_code", "employee__last_name", "note"]
