from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", auth_views.LoginView.as_view(template_name="web/login.html", redirect_authenticated_user=True),
         name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("company/", views.switch_company, name="switch_company"),
    path("attendance/", views.attendance, name="attendance"),
    path("attendance/unknown/", views.unknown_clocks, name="unknown_clocks"),
    path("employees/", views.employees, name="employees"),
    path("employees/<int:pk>/", views.employee_detail, name="employee"),
    path("pay/", views.pay_runs, name="pay_runs"),
    path("pay/new/", views.pay_run_new, name="pay_run_new"),
    path("pay/<int:pk>/", views.pay_run, name="pay_run"),
    path("pay/<int:pk>/amounts/", views.pay_run_amounts, name="pay_run_amounts"),
    path("pay/<int:pk>/import/", views.pay_run_import, name="pay_run_import"),
    path("pay/<int:pk>/<slug:action>/", views.pay_run_action, name="pay_run_action"),
    path("readers/", views.readers, name="readers"),
]
