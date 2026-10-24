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
    assert client.get("/apply").status_code == 200
    with client.session_transaction() as session:
        captcha = session["captcha_answer"]
    response = client.post("/apply", data={"mobile": mobile, "captcha": str(captcha)})
    assert response.status_code == 302 and response.headers["Location"].endswith("/verify")
    assert client.get("/__gateway/").status_code == 200
    end = time.monotonic() + 8
    messages = []
    while time.monotonic() < end:
        response = client.get("/__gateway/api/messages", query_string={"to": mobile})
        assert response.status_code == 200
        messages = response.json["messages"]
        if messages and messages[-1]["delivered"]:
            break
        time.sleep(0.05)
    assert messages and messages[-1]["delivered"]
    code = messages[-1]["text"].split("OTP is ")[1].split(".")[0]
    response = client.post("/verify", data={"otp": code})
    assert response.status_code == 302 and response.headers["Location"].endswith("/form/1")
    steps = [
        {"applicant_name": "Synthetic Citizen", "dob": "13/12/1950", "gender": "Male", "marital_status": "Single"},
        {"village": "Synthetic Village", "block": portal.BLOCKS[0]},
        {"bank_account": "000012345678", "ifsc": "TEST0000001"},
    ]
    for step, fields in enumerate(steps, 1):
        assert client.get(f"/form/{step}").status_code == 200
        assert client.post(f"/form/{step}", data=fields).status_code == 302
    proof = FPDF()
    proof.add_page()
    proof.set_font("Helvetica", size=10)
    proof.cell(50, 10, "Synthetic proof fixture")
    response = client.post("/upload", data={"document": (io.BytesIO(bytes(proof.output())), "proof.pdf")})
    assert response.status_code == 302 and response.headers["Location"].endswith("/declaration")
    assert client.get("/declaration").status_code == 200
    response = client.post("/declaration", data={"declaration": "yes"})
    assert response.status_code == 200
    with closing(database.connect()) as conn, conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, application_no, applicant_name, dob::text, bank_account, status FROM applications")
            rows = cur.fetchall()
    assert len(rows) == 1
    app_id, number, name, dob, account, status = rows[0]
    assert (name, dob, account, status) == ("Synthetic Citizen", "1950-12-13", "000012345678", "PENDING")
    assert number.encode() in response.data
    response = client.post("/status", data={"mobile": mobile, "password": "13121950"}, follow_redirects=True)
    assert response.status_code == 200 and number.encode() in response.data
    assert account.encode() in response.data
    receipt = client.get(f"/ack/{app_id}.pdf")
    assert receipt.status_code == 200 and receipt.data.startswith(b"%PDF-")
    staff = portal.app.test_client()
    response = staff.post("/admin", data={"username": portal.ADMIN_USERNAME, "password": portal.ADMIN_PASSWORD})
    assert response.status_code == 302
    for path in ["/admin/dashboard", "/admin/applications", f"/admin/application/{app_id}"]:
        assert staff.get(path).status_code == 200
    assert staff.post(f"/admin/approve/{app_id}").status_code == 302
    with closing(database.connect()) as conn, conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM applications WHERE id=%s", (app_id,))
            assert cur.fetchone()[0] == "APPROVED"
