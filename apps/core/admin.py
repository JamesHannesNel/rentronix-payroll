from django import forms
from django.contrib import admin, messages

from .models import ApiKey, AuditLog, Company, Membership


class ApiKeyForm(forms.ModelForm):
    scopes = forms.MultipleChoiceField(choices=ApiKey.Scope.choices, widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = ApiKey
        fields = ["company", "name", "scopes", "is_active", "expires_at"]


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    autocomplete_fields = ["user"]


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ["name", "trading_name", "paye_reference", "is_active"]
    search_fields = ["name", "trading_name"]
    inlines = [MembershipInline]


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    form = ApiKeyForm
    list_display = ["name", "company", "prefix", "scope_list", "is_active", "expires_at", "last_used_at"]
    list_filter = ["is_active", "company"]
    readonly_fields = ["prefix", "last_used_at", "created_at"]
    fields = ["company", "name", "scopes", "is_active", "expires_at", "prefix", "last_used_at", "created_at"]

    @admin.display(description="Scopes")
    def scope_list(self, obj):
        return ", ".join(obj.scopes or [])

    def get_changeform_initial_data(self, request):
        return {"scopes": [ApiKey.Scope.EMPLOYEE_READ, ApiKey.Scope.CLOCK_WRITE, ApiKey.Scope.DEVICE_WRITE]}

    def save_model(self, request, obj, form, change):
        if change:
            return super().save_model(request, obj, form, change)
        api_key, raw_key = ApiKey.issue(obj.company, obj.name, obj.scopes, obj.expires_at)
        obj.pk = api_key.pk
        self.message_user(
            request,
            f"API key created. Copy it now; it will not be shown again: {raw_key}",
            level=messages.WARNING,
        )


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "company", "actor", "action", "target"]
    list_filter = ["action", "company"]
    search_fields = ["actor", "target"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
