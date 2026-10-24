"""Behavioral security regressions: expected failures on the committed handout baseline."""
import hashlib
from datetime import timedelta

import pytest
from flask import Flask
from flask.sessions import SecureCookieSessionInterface

from security_support import (HANDOUT_ADMIN, HANDOUT_SIGNING_KEY, application_challenge,
    cache_receipt, citizen_login, cookie_value, copy_cookie, csrf_fields, delivered_code,
    denied, post, query, readable_cookie, security, staff_login, verified_application)


@pytest.mark.parametrize("cached", [False, True], ids=["uncached", "cached"])
def test_other_citizen_cannot_read_receipt(security, cached):
    target = security.people[1]
    if cached:
        cache_receipt(security, target["id"])
    attacker = citizen_login(security)
    denied(attacker.get(f"/ack/{target['id']}.pdf"))


@pytest.mark.parametrize("cached", [False, True], ids=["uncached", "cached"])
def test_anonymous_cannot_read_receipt(security, cached):
    target = security.people[1]
    if cached:
        cache_receipt(security, target["id"])
    denied(security.portal.app.test_client().get(f"/ack/{target['id']}.pdf"))


def test_other_citizen_cannot_read_detail(security):
    target = security.people[1]
    denied(citizen_login(security).get(f"/application/{target['id']}"), target["bank"])


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_citizen_cannot_make_staff_decision(security, action):
    target = security.people[1]["id"]
    client = citizen_login(security)
    response = post(client, f"/admin/{action}/{target}", page="/admin")
    status = query(security, "SELECT status FROM applications WHERE id=%s", (target,))[0][0]
    assert status == "PENDING", "Citizen changed a staff-only entitlement decision"
    assert response.status_code in (302, 303, 401, 403, 404)


@pytest.mark.parametrize("injected", [False, True], ids=["unverified-contact", "sql-injection"])
def test_recovery_cannot_change_password_without_proof(security, injected):
    # Use a non-DOB password so an unauthorized reset cannot masquerade as an unchanged hash.
    original = hashlib.sha256(b"Synthetic Original Password").hexdigest()
    query(security, "UPDATE portal_users SET password_hash=%s", (original,))
    before = query(security, "SELECT mobile,password_hash FROM portal_users ORDER BY mobile")
    mobile = "x' OR '1'='1" if injected else security.people[0]["mobile"]
    post(security.portal.app.test_client(), "/status/reset", {"mobile": mobile})
    after = query(security, "SELECT mobile,password_hash FROM portal_users ORDER BY mobile")
    assert after == before, "Recovery changed an account before ownership proof"


def test_published_admin_credentials_are_inactive(security):
    client = security.portal.app.test_client()
    post(client, "/admin", {"username": HANDOUT_ADMIN[0], "password": HANDOUT_ADMIN[1]})
    denied(client.get("/admin/dashboard"))


def test_cookie_signed_with_published_key_cannot_grant_staff_role(security):
    attacker = Flask("synthetic_cookie_attacker")
    attacker.secret_key = HANDOUT_SIGNING_KEY
    serializer = SecureCookieSessionInterface().get_signing_serializer(attacker)
    forged = serializer.dumps({"logged_in": True, "admin": True, "portal_mobile": security.people[0]["mobile"]})
    client = security.portal.app.test_client()
    client.set_cookie(security.portal.app.config.get("SESSION_COOKIE_NAME", "session"), forged)
    denied(client.get("/admin/dashboard"))


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("token", [None, "invalid"], ids=["missing", "invalid"])
def test_staff_decision_requires_csrf(security, action, token):
    client = staff_login(security)
    target = security.people[0]["id"]
    data = {} if token is None else {"csrf_token": token}
    response = client.post(f"/admin/{action}/{target}", data=data,
                           headers={"Origin": "https://synthetic-attacker.invalid"})
    status = query(security, "SELECT status FROM applications WHERE id=%s", (target,))[0][0]
    assert status == "PENDING", "CSRF changed a staff decision"
    assert response.status_code in (400, 403), "Missing/invalid CSRF was not rejected"


def test_staff_login_without_csrf_cannot_establish_staff_context(security):
    client = security.portal.app.test_client()
    username = getattr(security.portal, "ADMIN_USERNAME", security.staff_name)
    password = getattr(security.portal, "ADMIN_PASSWORD", security.staff_password)
    client.post("/admin", data={"username": username, "password": password})
    denied(client.get("/admin/dashboard"))


def test_citizen_login_drops_previous_staff_context(security):
    client = staff_login(security)
    citizen_login(security, client=client)
    denied(client.get("/admin/dashboard"))


def test_form_identity_and_bank_details_are_not_readable_in_cookie(security):
    client = security.portal.app.test_client()
    verified_application(security, client)
    name, bank = "Synthetic Private Person", "0000999988887777"
    post(client, "/form/1", {"applicant_name": name, "dob": "13/12/1950", "gender": "Male", "marital_status": "Unmarried"})
    post(client, "/form/2", {"village": "Synthetic Village", "block": "Sonari"})
    post(client, "/form/3", {"bank_account": bank, "ifsc": "TEST0000001"})
    public_payload = readable_cookie(cookie_value(client, security.portal))
    assert name.encode() not in public_payload, "Client-readable session exposed identity"
    assert bank.encode() not in public_payload, "Client-readable session exposed bank data"


def test_logout_revokes_replayed_authenticated_cookie(security):
    client = citizen_login(security)
    replay = security.portal.app.test_client()
    copy_cookie(client, replay, security.portal)
    post(client, "/logout", page="/status")
    denied(replay.get(f"/application/{security.people[0]['id']}"))


def test_legacy_dob_password_alone_does_not_grant_record_access(security):
    client = security.portal.app.test_client()
    person = security.people[0]
    post(client, "/status", {"mobile": person["mobile"], "password": person["password"]})
    denied(client.get(f"/application/{person['id']}"))


def test_consumed_otp_cannot_authenticate_a_replayed_challenge(security):
    client = security.portal.app.test_client()
    application_challenge(security, client)
    code = delivered_code(client, security.people[0]["mobile"])
    replay = security.portal.app.test_client()
    copy_cookie(client, replay, security.portal)
    post(client, "/verify", {"otp": code})
    assert client.get("/form/1").status_code == 200
    post(replay, "/verify", {"otp": code})
    denied(replay.get("/form/1"))


def test_otp_attempt_limit_prevents_late_correct_guess(security):
    client = security.portal.app.test_client()
    application_challenge(security, client)
    code = delivered_code(client, security.people[0]["mobile"])
    wrong = "999999" if code != "999999" else "888888"
    for _ in range(5):
        post(client, "/verify", {"otp": wrong})
    post(client, "/verify", {"otp": code})
    denied(client.get("/form/1"))


def test_expired_otp_cannot_open_form(security):
    client = security.portal.app.test_client()
    application_challenge(security, client)
    code = delivered_code(client, security.people[0]["mobile"])
    security.clock.instant += timedelta(seconds=301)
    post(client, "/verify", {"otp": code})
    denied(client.get("/form/1"))


def test_wrong_captcha_does_not_send_otp(security):
    client = security.portal.app.test_client()
    application_challenge(security, client, captcha_valid=False)
    assert not security.gateway.MESSAGES, "Invalid captcha still sent an OTP"


@pytest.mark.parametrize("cached", [False, True], ids=["uncached", "cached"])
def test_owner_can_read_own_detail_and_receipt(security, cached):
    person = security.people[0]
    if cached:
        cache_receipt(security, person["id"])
    client = citizen_login(security)
    detail = client.get(f"/application/{person['id']}")
    assert detail.status_code == 200 and person["bank"].encode() in detail.data
    receipt = client.get(f"/ack/{person['id']}.pdf")
    assert receipt.status_code == 200 and receipt.data.startswith(b"%PDF-")


def test_staff_can_read_records_and_make_valid_decision(security):
    client = staff_login(security)
    target = security.people[1]["id"]
    assert client.get(f"/admin/application/{target}").status_code == 200
    receipt = client.get(f"/ack/{target}.pdf")
    assert receipt.status_code == 200 and receipt.data.startswith(b"%PDF-")
    assert post(client, f"/admin/approve/{target}", page="/admin").status_code in (302, 303)
    assert query(security, "SELECT status FROM applications WHERE id=%s", (target,))[0][0] == "APPROVED"


def test_required_gateway_console_and_delivered_inbox_remain_usable(security):
    client = security.portal.app.test_client()
    assert client.get("/__gateway/").status_code == 200
    application_challenge(security, client)
    code = delivered_code(client, security.people[0]["mobile"])
    assert len(code) == 6
    post(client, "/verify", {"otp": code})
    assert client.get("/form/1").status_code == 200


@pytest.mark.parametrize("resource", ["detail", "receipt"])
def test_sensitive_responses_are_not_cacheable(security, resource):
    client = citizen_login(security)
    target = security.people[0]["id"]
    path = f"/application/{target}" if resource == "detail" else f"/ack/{target}.pdf"
    response = client.get(path)
    assert response.status_code == 200
    assert "no-store" in response.headers.get("Cache-Control", ""), "Sensitive response lacks no-store policy"


def test_otp_contact_send_budget_spans_sessions_and_app_instances(security, monkeypatch):
    from conftest import ROOT, load_module
    second = load_module("security_second_portal", ROOT / "app/app.py")
    second.app.config["TESTING"] = True
    monkeypatch.setattr(second, "get_db", security.database.connect)
    monkeypatch.setattr(second, "SMS_GATEWAY_URL", security.portal.SMS_GATEWAY_URL)
    monkeypatch.setattr(second, "datetime", security.portal.datetime)
    for index in range(11):
        app = security.portal.app if index % 2 == 0 else second.app
        application_challenge(security, app.test_client())
    sent = len(security.gateway.MESSAGES)
    assert sent <= 10, "Contact send budget was bypassed with fresh sessions/app instances"
