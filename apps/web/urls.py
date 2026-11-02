from django.contrib.auth import views as auth_views
from django.urls import path

from . import views, views_hours

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
    path("employees/export/", views.employees_export, name="employees_export"),
    path("employees/<int:pk>/", views.employee_detail, name="employee"),
    path("timesheets/", views_hours.periods, name="periods"),
    path("timesheets/new/", views_hours.period_new, name="period_new"),
    path("timesheets/<int:pk>/", views_hours.period, name="period"),
    path("timesheets/<int:pk>/people/<int:employee_pk>/", views_hours.period_employee, name="period_employee"),
    path("timesheets/<int:pk>/people/<int:employee_pk>/save/", views_hours.period_employee_save,
         name="period_employee_save"),
    path("timesheets/<int:pk>/<slug:action>/", views_hours.period_action, name="period_action"),
    path("loans/", views_hours.loans, name="loans"),
    path("loans/<int:pk>/<slug:action>/", views_hours.loan_action, name="loan_action"),
    path("savings/<int:pk>/<slug:action>/", views_hours.saving_action, name="saving_action"),
    path("settings/", views_hours.settings_page, name="settings"),
    path("settings/api-keys/", views_hours.api_keys, name="api_keys"),
    path("pay/", views.pay_runs, name="pay_runs"),
    path("pay/new/", views.pay_run_new, name="pay_run_new"),
    path("pay/<int:pk>/", views.pay_run, name="pay_run"),
    path("pay/<int:pk>/amounts/", views.pay_run_amounts, name="pay_run_amounts"),
    path("pay/<int:pk>/import/", views.pay_run_import, name="pay_run_import"),
    path("pay/<int:pk>/<slug:action>/", views.pay_run_action, name="pay_run_action"),
    path("readers/", views.readers, name="readers"),
]
