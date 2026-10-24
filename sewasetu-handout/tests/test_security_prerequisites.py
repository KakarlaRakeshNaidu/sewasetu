"""Pre-commit overlap proof for Phase 3 configuration, sessions, DB and upload security."""
import io
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fpdf import FPDF

from security_support import (ROOT, csrf_fields, post, query, security, staff_login, verified_application)


def test_verified_form_session_survives_ten_minutes_below_idle_limit(security):
    client = security.portal.app.test_client()
    verified_application(security, client)
    post(client, "/form/1", {"applicant_name": "Synthetic Saved Name", "dob": "13/12/1950", "gender":"Male", "marital_status":"Unmarried"})
    security.clock.instant += timedelta(minutes=10)
    response = client.get("/form/2")
    assert response.status_code == 200, "Form session expired at the OTP lifetime rather than its idle limit"


def test_reverification_resumes_owned_saved_draft(security):
    client = security.portal.app.test_client()
    verified_application(security, client)
    marker = "Synthetic Saved Name"
    post(client, "/form/1", {"applicant_name": marker, "dob": "13/12/1950"})
    security.clock.instant += timedelta(minutes=31)
    client.get("/form/1")  # Enforce expired authentication before a new verification.
    verified_application(security, client)
    response = client.get("/form/1")
    assert marker.encode() in response.data, "Authorized reverification lost the saved draft"


@pytest.mark.parametrize("instant,server_zone,expected_open", [
    ("2026-10-31T23:59:30+05:30", "Asia/Kolkata", True),
    ("2026-10-31T23:59:59+05:30", "UTC", True),
    ("2026-11-01T00:00:00+05:30", "UTC", False),
    ("2026-11-01T00:00:01+05:30", "UTC", False),
], ids=["ist-final-minute", "utc-before", "utc-at", "utc-after"])
def test_exclusive_ist_intake_boundary_with_server_timezone(security, instant, server_zone, expected_open):
    security.clock.instant = datetime.fromisoformat(instant)
    security.clock.server_zone = ZoneInfo(server_zone)
    response = security.portal.app.test_client().get("/apply")
    assert (response.status_code == 200) is expected_open, "Intake did not obey the configured exclusive IST midnight"
    assert security.portal.app.test_client().get("/status").status_code == 200
    assert security.portal.app.test_client().get("/__gateway/").status_code == 200


def test_db_error_rolls_back_releases_connections_and_allows_recovery(security, monkeypatch):
    client = staff_login(security)
    target = security.people[0]["id"]
    # A real PostgreSQL trigger faults the real decision transaction, not a mocked cursor.
    query(security, """CREATE FUNCTION synthetic_decision_fault() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic transient database failure'; END $$""")
    query(security, "CREATE TRIGGER synthetic_decision_fault BEFORE UPDATE ON applications FOR EACH ROW EXECUTE FUNCTION synthetic_decision_fault()")
    opened = []
    def connect():
        connection = security.database.connect()
        opened.append(connection)  # Delay GC to observe explicit rollback/release behavior.
        return connection
    monkeypatch.setattr(security.portal, "get_db", connect)
    monkeypatch.setitem(security.portal.app.config, "TESTING", False)
    try:
        for _ in range(3):
            response = post(client, f"/admin/approve/{target}", page="/admin")
            assert response.status_code in (500, 503)
        aborted = query(security, "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND state LIKE %s",
                        ("idle in transaction%",))[0][0]
        query(security, "DROP TRIGGER synthetic_decision_fault ON applications")
        query(security, "DROP FUNCTION synthetic_decision_fault()")
        assert client.get(f"/admin/application/{target}").status_code == 200
    finally:
        for connection in opened:
            connection.close()
    assert aborted == 0, "Failed requests retained idle/aborted database transactions"


def test_overdue_record_is_processed_by_real_job_entrypoint(security):
    # Supports config/job overlap proof; cron is never started and only this owned DB is targeted.
    instant = datetime.now(ZoneInfo("Asia/Kolkata")) - timedelta(days=16)
    query(security, "UPDATE applications SET submitted_at=%s", (instant.replace(tzinfo=None),))
    environment = dict(os.environ, SMS_GATEWAY_URL=security.portal.SMS_GATEWAY_URL)
    result = subprocess.run([sys.executable, "scripts/deemed_approval.py"], cwd=ROOT / "app",
                            env=environment, capture_output=True, text=True, timeout=20)
    statuses = query(security, "SELECT status FROM applications ORDER BY id")
    # Primary failure is absent statutory processing, not the captured missing-config traceback.
    assert all(row[0] == "DEEMED_APPROVED" for row in statuses), "Real job left overdue synthetic records pending"
    assert result.returncode == 0


def test_upload_path_input_does_not_escape_or_crash(security, monkeypatch):
    client = security.portal.app.test_client()
    verified_application(security, client)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    pdf.cell(40, 10, "Synthetic proof")
    monkeypatch.setitem(security.portal.app.config, "TESTING", False)
    response = client.post("/upload", data=csrf_fields(client, "/upload") | {
        "document": (io.BytesIO(bytes(pdf.output())), "../../synthetic-escape.pdf")})
    assert response.status_code in (200, 302, 303, 400, 413, 415, 422), "Untrusted upload path caused a server error"
    assert not (Path(security.portal.UPLOAD_DIR).parent / "synthetic-escape.pdf").exists()

