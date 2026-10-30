from django.contrib import admin

from .models import ClockRecord, Device


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ["serial", "name", "company", "site", "is_online", "last_seen_at", "firmware"]
    list_filter = ["company", "is_online", "site"]
    search_fields = ["serial", "name"]


class UnmatchedFilter(admin.SimpleListFilter):
    title = "employee matched"
    parameter_name = "matched"

    def lookups(self, request, model_admin):
        return [("yes", "Matched"), ("no", "Unknown employee code")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(employee__isnull=False)
        if self.value() == "no":
            return queryset.filter(employee__isnull=True)
        return queryset


@admin.register(ClockRecord)
class ClockRecordAdmin(admin.ModelAdmin):
    list_display = ["clocked_at", "employee_code", "employee", "direction", "method", "reader_serial", "source",
                    "received_at"]
    list_filter = ["company", UnmatchedFilter, "direction", "source", "device"]
    search_fields = ["employee_code", "employee__first_name", "employee__last_name", "reader_serial"]
    date_hierarchy = "clocked_at"
    readonly_fields = [f.name for f in ClockRecord._meta.fields]

    def has_add_permission(self, request):
        return False  # manual clocks get their own audited screen in Phase 1
