"""Integration API for clocking partners (Bio3G first).

All requests act for the company that owns the API key. See
docs/INTEGRATION-API.md for the partner-facing description.
"""

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import ApiKey, audit
from apps.employees.models import Employee

from .models import ClockRecord, Device
from .serializers import (
    ClockBatchSerializer,
    ClockInSerializer,
    ClockOutSerializer,
    DeviceStatusSerializer,
    EmployeeOutSerializer,
    EnrolmentSerializer,
)

PAGE_SIZE = 500


def _actor(request):
    return f"api:{request.auth.prefix}"


def _parse_since(request, name):
    raw = request.query_params.get(name)
    if not raw:
        return None
    value = parse_datetime(raw)
    if value is None or value.tzinfo is None:
        raise ValidationError({name: "Use ISO 8601 with a UTC offset, e.g. 2026-11-01T00:00:00+02:00."})
    return value


def _paged(request, queryset, serializer_class):
    try:
        offset = max(int(request.query_params.get("offset", 0)), 0)
    except ValueError:
        raise ValidationError({"offset": "Must be a whole number."}) from None
    rows = list(queryset[offset:offset + PAGE_SIZE + 1])
    body = {"results": serializer_class(rows[:PAGE_SIZE], many=True).data}
    if len(rows) > PAGE_SIZE:
        body["next_offset"] = offset + PAGE_SIZE
    return Response(body)


class EmployeeListView(APIView):
    """GET /api/v1/employees?updated_since=… — employees for the readers."""

    required_scopes = {"GET": ApiKey.Scope.EMPLOYEE_READ}

    def get(self, request):
        qs = Employee.objects.filter(company=request.auth.company).select_related("site").order_by("updated_at", "id")
        since = _parse_since(request, "updated_since")
        if since:
            qs = qs.filter(updated_at__gt=since)
        return _paged(request, qs, EmployeeOutSerializer)


class ClockView(APIView):
    """POST /api/v1/clocks — import a batch; GET /api/v1/clocks?since=… — read them back."""

    required_scopes = {"POST": ApiKey.Scope.CLOCK_WRITE, "GET": ApiKey.Scope.CLOCK_READ}

    def get(self, request):
        qs = ClockRecord.objects.filter(company=request.auth.company).order_by("received_at", "id")
        since = _parse_since(request, "since")
        if since:
            qs = qs.filter(received_at__gt=since)
        return _paged(request, qs, ClockOutSerializer)

    def post(self, request):
        company = request.auth.company
        batch = ClockBatchSerializer(data=request.data)
        batch.is_valid(raise_exception=True)

        valid, errors = [], []
        for index, raw in enumerate(batch.validated_data["clocks"]):
            item = ClockInSerializer(data=raw)
            if item.is_valid():
                valid.append(item.validated_data)
            else:
                errors.append({"index": index, "errors": item.errors})

        employees = dict(
            Employee.objects.filter(company=company, employee_code__in={c["employee_code"] for c in valid})
            .values_list("employee_code", "id")
        )
        devices = {}
        for serial in {c["reader_serial"] for c in valid}:
            devices[serial], _ = Device.objects.get_or_create(company=company, serial=serial)

        existing = set(
            ClockRecord.objects.filter(company=company, reader_serial__in=devices.keys(),
                                       record_id__in={c["record_id"] for c in valid})
            .values_list("reader_serial", "record_id")
        )
        seen, new_records, unmatched = set(), [], 0
        for c in valid:
            key = (c["reader_serial"], c["record_id"])
            if key in existing or key in seen:
                continue
            seen.add(key)
            employee_id = employees.get(c["employee_code"])
            unmatched += employee_id is None
            new_records.append(ClockRecord(
                company=company, employee_id=employee_id, employee_code=c["employee_code"],
                device=devices[c["reader_serial"]], reader_serial=c["reader_serial"], record_id=c["record_id"],
                clocked_at=c["clocked_at"], direction=c["direction"], method=c["method"],
                source=ClockRecord.Source.BIO3G if "bio3g" in request.auth.name.lower() else ClockRecord.Source.API,
            ))

        with transaction.atomic():
            ClockRecord.objects.bulk_create(new_records, ignore_conflicts=True)
            Device.objects.filter(pk__in=[d.pk for d in devices.values()]).update(
                last_seen_at=timezone.now(), is_online=True)
            if new_records:
                audit("clocks.import", actor=_actor(request), company=company, target="clocks",
                      accepted=len(new_records), unmatched=unmatched)

        duplicates = len(valid) - len(new_records)
        body = {"accepted": len(new_records), "duplicates": duplicates, "unmatched_employee": unmatched,
                "rejected": len(errors), "errors": errors}
        return Response(body, status=status.HTTP_200_OK if not errors else status.HTTP_207_MULTI_STATUS)


class EnrolmentView(APIView):
    """POST /api/v1/enrolments — which employees are enrolled on the readers."""

    required_scopes = {"POST": ApiKey.Scope.DEVICE_WRITE}

    def post(self, request):
        items = EnrolmentSerializer(data=request.data.get("enrolments", []), many=True)
        items.is_valid(raise_exception=True)
        now, updated = timezone.now(), 0
        for item in items.validated_data:
            updated += Employee.objects.filter(company=request.auth.company, employee_code=item["employee_code"]).update(
                biometric_enrolled=item["enrolled"], biometric_method=item["method"], biometric_checked_at=now)
        return Response({"updated": updated, "unknown": len(items.validated_data) - updated})


class DeviceStatusView(APIView):
    """POST /api/v1/devices/status — reader online status, hourly."""

    required_scopes = {"POST": ApiKey.Scope.DEVICE_WRITE}

    def post(self, request):
        items = DeviceStatusSerializer(data=request.data.get("devices", []), many=True)
        items.is_valid(raise_exception=True)
        for item in items.validated_data:
            Device.objects.update_or_create(
                company=request.auth.company, serial=item["reader_serial"],
                defaults={"is_online": item["online"], "last_seen_at": item.get("last_seen_at") or timezone.now(),
                          **({"firmware": item["firmware"]} if item.get("firmware") else {})})
        return Response({"updated": len(items.validated_data)})
