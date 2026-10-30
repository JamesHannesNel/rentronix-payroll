from django.contrib import admin, messages

from .client import PaySpaceError
from .models import ComponentCode, PayFrequency, PaySpaceLink, SyncRun
from .sync import pull_company


@admin.register(PaySpaceLink)
class PaySpaceLinkAdmin(admin.ModelAdmin):
    list_display = ["company", "payspace_company_id", "payspace_company_name", "is_active", "last_pulled_at"]
    readonly_fields = ["last_pulled_at"]
    actions = ["pull_now"]

    @admin.action(description="Pull from PaySpace now")
    def pull_now(self, request, queryset):
        for link in queryset:
            try:
                result = pull_company(link, actor=f"user:{request.user.get_username()}")
            except PaySpaceError as exc:
                self.message_user(request, f"{link.company}: {exc}", level=messages.ERROR)
                continue
            self.message_user(request, f"{link.company}: {result.counts()}", level=messages.SUCCESS)
            for warning in result.warnings:
                self.message_user(request, f"{link.company}: {warning}", level=messages.WARNING)


@admin.register(PayFrequency)
class PayFrequencyAdmin(admin.ModelAdmin):
    list_display = ["name", "payspace_id", "company"]
    list_filter = ["company"]


@admin.register(ComponentCode)
class ComponentCodeAdmin(admin.ModelAdmin):
    list_display = ["code", "description", "company"]
    list_filter = ["company"]
    search_fields = ["code", "description"]


@admin.register(SyncRun)
class SyncRunAdmin(admin.ModelAdmin):
    list_display = ["started_at", "company", "kind", "status", "counts", "error"]
    list_filter = ["status", "company"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
