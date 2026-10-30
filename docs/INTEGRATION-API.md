# Integration API (v1)

For clocking partners such as Biometrics 3G. Every request acts for the one
client company that owns the API key.

## Authentication

```
Authorization: Bearer rp_<prefix>_<secret>
```

- One key per client company, issued by Rentronix (`manage.py issue_api_key`
  or the admin). The key is shown once; only a hash is stored.
- Scopes: `employee:read`, `clock:read`, `clock:write`, `device:write`.
- Limit: 60 requests a minute per key (HTTP 429 when exceeded).
- HTTPS only in production.

## Endpoints

### GET /api/v1/employees

Query: `updated_since` (ISO 8601 with offset), `offset` (paging).
Returns up to 500 employees per page, oldest change first, plus
`next_offset` when there are more.

```json
{"results": [{"employee_code": "EMP0042", "first_name": "Sipho", "last_name": "Dlamini",
  "preferred_name": "", "site": "Umhlanga site", "status": "active",
  "biometric_enrolled": true, "updated_at": "2026-11-02T08:00:00+02:00"}]}
```

Use `status` to remove terminated employees from readers.

### POST /api/v1/clocks

Up to 1,000 records per call.

```json
{"clocks": [{"reader_serial": "FA1000-00123", "record_id": "884512",
  "employee_code": "EMP0042", "clocked_at": "2026-11-02T06:58:41+02:00",
  "direction": "in", "method": "face"}]}
```

- `clocked_at` is the reader's own timestamp. A time without an offset is
  read as South African time (UTC+2).
- `direction`: `in`, `out` or `unknown`.
- Re-sending is safe: `reader_serial` + `record_id` identify a record and
  duplicates are ignored.
- Unknown employee codes are kept and flagged for the payroll administrator.

Response (HTTP 200, or 207 if some rows were rejected):

```json
{"accepted": 1, "duplicates": 0, "unmatched_employee": 0, "rejected": 0, "errors": []}
```

### POST /api/v1/enrolments

```json
{"enrolments": [{"employee_code": "EMP0042", "enrolled": true, "method": "face"}]}
```

Only whether an employee is enrolled. Biometric templates are never sent.

### POST /api/v1/devices/status

```json
{"devices": [{"reader_serial": "FA1000-00123", "online": true,
  "last_seen_at": "2026-11-02T09:00:00+02:00", "firmware": "6.60"}]}
```

### GET /api/v1/clocks

Query: `since` (records received after this time), `offset`. Needs `clock:read`.
