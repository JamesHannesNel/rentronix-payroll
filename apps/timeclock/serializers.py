from rest_framework import serializers

from apps.employees.models import Employee

from .models import ClockRecord

MAX_BATCH = 1000


class EmployeeOutSerializer(serializers.ModelSerializer):
    site = serializers.CharField(source="site.name", default="", read_only=True)

    class Meta:
        model = Employee
        fields = ["employee_code", "first_name", "last_name", "preferred_name", "site", "status",
                  "biometric_enrolled", "updated_at"]


class ClockInSerializer(serializers.Serializer):
    reader_serial = serializers.CharField(max_length=60)
    record_id = serializers.CharField(max_length=60)
    employee_code = serializers.CharField(max_length=30)
    clocked_at = serializers.DateTimeField()
    direction = serializers.ChoiceField(choices=ClockRecord.Direction.values, required=False,
                                        default=ClockRecord.Direction.UNKNOWN)
    method = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    # A time sent without a UTC offset is read as South African time (UTC+2, no daylight saving).


class ClockBatchSerializer(serializers.Serializer):
    clocks = serializers.ListField(child=serializers.DictField(), allow_empty=False, max_length=MAX_BATCH)


class ClockOutSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClockRecord
        fields = ["reader_serial", "record_id", "employee_code", "clocked_at", "direction", "method", "source",
                  "received_at"]


class EnrolmentSerializer(serializers.Serializer):
    employee_code = serializers.CharField(max_length=30)
    enrolled = serializers.BooleanField()
    method = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")


class DeviceStatusSerializer(serializers.Serializer):
    reader_serial = serializers.CharField(max_length=60)
    online = serializers.BooleanField()
    last_seen_at = serializers.DateTimeField(required=False)
    firmware = serializers.CharField(max_length=60, required=False, allow_blank=True)
