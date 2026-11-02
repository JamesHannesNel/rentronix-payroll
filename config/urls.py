from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def healthz(request):
    return JsonResponse({"status": "ok"})


admin.site.site_header = "Rentronix Payroll"
admin.site.site_title = "Rentronix Payroll"
admin.site.index_title = "Administration"

urlpatterns = [
    path("healthz", healthz),
    path("admin/", admin.site.urls),
    path("api/v1/", include("apps.timeclock.urls")),
    path("", include("apps.web.urls")),
]
