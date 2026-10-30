from django.urls import path

from . import views

urlpatterns = [
    path("employees", views.EmployeeListView.as_view()),
    path("clocks", views.ClockView.as_view()),
    path("enrolments", views.EnrolmentView.as_view()),
    path("devices/status", views.DeviceStatusView.as_view()),
]
