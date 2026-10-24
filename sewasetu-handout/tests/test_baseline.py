from contextlib import closing
"""Inherited supported journeys; selected-fix red tests come in later checkpoints."""
import io
import time

import pytest
from fpdf import FPDF


@pytest.mark.parametrize("path", ["/", "/about", "/apply", "/status", "/status/reset", "/admin"])
def test_public_entry_points(portal, path):
    assert portal.app.test_client().get(path).status_code == 200


def test_anonymous_status_and_staff_pages_require_login(portal):
    client = portal.app.test_client()
    assert client.get("/application/1").headers["Location"].endswith("/status")
    for path in ["/admin/dashboard", "/admin/applications", "/admin/application/1"]:
        assert client.get(path).headers["Location"].endswith("/admin")


def test_citizen_application_receipt_status_and_staff_decision(portal, gateway, database):
    client = portal.app.test_client()
    mobile = "9000000000"  # Synthetic fixture, never copied from the seed.
    from submission_support import prepare, login, post_form
    from security_support import delivered_code
    data = prepare(client,mobile,name='Synthetic Citizen')
    response = client.post('/declaration',data=data,follow_redirects=True)
    assert response.status_code == 200
    with closing(database.connect()) as conn, conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id,application_no,applicant_name,dob::text,bank_account,status FROM applications')
            rows = cur.fetchall()
    assert len(rows) == 1
    app_id,number,name,dob,account,status = rows[0]
    assert (name,dob,account,status) == ('Synthetic Citizen','1950-12-13','000012345678','PENDING')
    assert number.encode() in response.data
    # Already verified application contact can access its own accepted record.
    assert client.get(f'/application/{app_id}').status_code == 200
    receipt = client.get(f'/ack/{app_id}.pdf')
    assert receipt.status_code == 200 and receipt.data.startswith(b'%PDF-')
    # A fresh citizen session requires a delivered OTP, including inherited accounts.
    citizen = portal.app.test_client()
    # Advance beyond send cooldown without changing the production clock/policy.
    from datetime import timedelta
    with closing(database.connect()) as conn, conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE portal_challenges SET created_at=created_at-interval '61 seconds'")
    response = login(citizen,mobile)
    assert response.status_code in (302,303)
    assert account.encode() in citizen.get(f'/application/{app_id}').data
    staff = portal.app.test_client()
    response = post_form(staff,'/admin',{'username':portal.ADMIN_USERNAME,'password':'Synthetic-Only-Staff-Password'})
    assert response.status_code == 302
    for path in ['/admin/dashboard','/admin/applications',f'/admin/application/{app_id}']:
        assert staff.get(path).status_code == 200
    assert post_form(staff,f'/admin/approve/{app_id}',page='/admin').status_code == 302
    with closing(database.connect()) as conn, conn:
        with conn.cursor() as cur:
            cur.execute('SELECT status FROM applications WHERE id=%s',(app_id,))
            assert cur.fetchone()[0] == 'APPROVED'
