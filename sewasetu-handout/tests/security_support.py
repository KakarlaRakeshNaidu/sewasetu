"""Synthetic PostgreSQL and real loopback HTTP helpers; no runtime security bypass."""
import base64
import hashlib
import re
import secrets
import threading
import time
import zlib
from contextlib import closing
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pytest
from itsdangerous import TimestampSigner
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server

from conftest import ROOT, load_module

# Published, compromised exercise defaults, never new active credentials.
HANDOUT_ADMIN = ("admin", "sewasetu@123")
HANDOUT_SIGNING_KEY = "ssp-prod-key-2023-donotshare"


class Inputs(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.fields = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name"):
            self.fields[attrs["name"]] = attrs.get("value", "")


def csrf_fields(client, page):
    fields = Inputs(client.get(page).get_data(as_text=True)).fields
    return {"csrf_token": fields["csrf_token"]} if "csrf_token" in fields else {}


def post(client, path, data=None, page=None):
    return client.post(path, data=csrf_fields(client, page or path) | (data or {}))


def cookie_value(client, module):
    cookie = client.get_cookie(module.app.config.get("SESSION_COOKIE_NAME", "session"))
    assert cookie is not None, "Expected a challenge/authenticated cookie"
    return cookie.value


def copy_cookie(source, target, module):
    target.set_cookie(module.app.config.get("SESSION_COOKIE_NAME", "session"), cookie_value(source, module))


def readable_cookie(value):
    try:
        parts = value.split(".")
        compressed = parts[0] == ""
        part = parts[1] if compressed else parts[0]
        result = base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
        return zlib.decompress(result) if compressed else result
    except (ValueError, zlib.error, IndexError):
        return b""


def denied(response, marker=None):
    assert response.status_code in (302, 303, 401, 403, 404), "Protected data/action was not denied"
    assert not response.data.startswith(b"%PDF-"), "Denied response exposed receipt bytes"
    if marker:
        assert marker.encode() not in response.data, "Denied response exposed another citizen's data"


def delivered_code(client, mobile):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get("/__gateway/api/messages", query_string={"to": mobile})
        assert response.status_code == 200, "Required gateway inbox unavailable"
        for message in reversed(response.json["messages"]):
            if message["delivered"] and message["text"]:
                match = re.search(r"\b\d{6}\b", message["text"])
                if match:
                    return match[0]
        time.sleep(.05)
    pytest.fail("Synthetic OTP was not delivered through gateway")


def application_challenge(ctx, client, mobile=None, *, captcha_valid=True):
    response = client.get("/apply")
    html = response.get_data(as_text=True)
    question = re.search(r"(\d+)\s*\+\s*(\d+)", html)
    assert question, "Application entry must expose a readable captcha"
    answer = int(question[1]) + int(question[2])
    fields = Inputs(html).fields
    data = {"mobile": mobile or ctx.people[0]["mobile"], "captcha": str(answer if captcha_valid else answer + 100)}
    if "csrf_token" in fields:
        data["csrf_token"] = fields["csrf_token"]
    return client.post("/apply", data=data)


def verified_application(ctx, client):
    response = application_challenge(ctx, client)
    assert response.status_code in (200, 302, 303)
    code = delivered_code(client, ctx.people[0]["mobile"])
    post(client, "/verify", {"otp": code})
    assert client.get("/form/1").status_code == 200, "Lawful application verification failed"
    return code


def citizen_login(ctx, person=0, client=None):
    client = client or ctx.portal.app.test_client()
    citizen = ctx.people[person]
    response = post(client, "/status", {"mobile": citizen["mobile"], "password": citizen["password"]})
    if "/status/verify" in response.headers.get("Location", "") or "otp" in Inputs(response.get_data(as_text=True)).fields:
        post(client, "/status/verify", {"otp": delivered_code(client, citizen["mobile"])})
    assert client.get(f"/application/{citizen['id']}").status_code == 200, "Lawful citizen login failed"
    return client


def staff_login(ctx, client=None):
    client = client or ctx.portal.app.test_client()
    post(client, "/admin", {"username": getattr(ctx.portal, "ADMIN_USERNAME", ctx.staff_name),
                            "password": getattr(ctx.portal, "ADMIN_PASSWORD", ctx.staff_password)})
    assert client.get("/admin/dashboard").status_code == 200, "Lawful staff login failed"
    return client


def query(ctx, sql, params=()):
    with closing(ctx.database.connect()) as conn, conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None


def cache_receipt(ctx, app_id):
    # Explicit fixture for the inherited existing-file fast path, not a fake PDF response.
    with closing(ctx.database.connect()) as conn:
        with conn.cursor() as cur:
            data = ctx.portal.generate_acknowledgment(cur, app_id)
    path = Path(ctx.portal.UPLOAD_DIR) / "ack" / f"{app_id}.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    assert path.is_file()


@pytest.fixture
def security(database, monkeypatch, tmp_path):
    dsn = "postgresql+psycopg2://sewasetu_test@/postgres?host=" + quote(database.connection["host"], safe="")
    staff_name, staff_password = "synthetic-staff", "Synthetic-Only-Staff-Password"
    settings = {"APP_ENV": "test", "PUBLIC_BASE_URL": "http://localhost", "SECRET_KEY": secrets.token_hex(32),
                "OTP_HMAC_KEY": secrets.token_hex(32), "DATABASE_URL": dsn,
                "ADMIN_USERNAME": staff_name, "ADMIN_PASSWORD_HASH": generate_password_hash(staff_password),
                "SESSION_COOKIE_SECURE": "false", "OTP_VALIDITY_SECONDS": "300", "OTP_MAX_ATTEMPTS": "5",
                "OTP_RESEND_COOLDOWN_SECONDS": "60", "OTP_MAX_SENDS_PER_HOUR": "10",
                "SESSION_IDLE_SECONDS": "1800", "SESSION_ABSOLUTE_SECONDS": "7200", "DRAFT_RETENTION_DAYS": "7",
                "APP_TIMEZONE": "Asia/Kolkata", "MIN_AGE": "60", "SLA_DAYS": "15",
                "SCHEME_DEADLINE": "2026-11-01T00:00:00+05:30", "UPLOAD_DIR": str(tmp_path / "uploads")}
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    with closing(database.connect()) as conn, conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE applications, portal_users, otps, web_sessions, portal_challenges, application_drafts, application_audit RESTART IDENTITY CASCADE")
    module = load_module("security_portal", ROOT / "app/app.py")
    module.app.config["TESTING"] = True
    # Leave runtime signing/admin secrets intact so default-secret tests detect real behavior.
    monkeypatch.setattr(module, "get_db", database.connect)
    monkeypatch.setattr(module, "UPLOAD_DIR", settings["UPLOAD_DIR"])
    clock = SimpleNamespace(instant=datetime(2026, 10, 24, 12, tzinfo=ZoneInfo("Asia/Kolkata")), server_zone=ZoneInfo("Asia/Kolkata"))
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.instant.astimezone(tz) if tz else clock.instant.astimezone(clock.server_zone).replace(tzinfo=None)
    monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: int(clock.instant.timestamp()))
    gateway = load_module("security_gateway", ROOT / "smsgw/app.py")
    gateway.DELAY_MIN = gateway.DELAY_MAX = 0  # Carrier latency only; real HTTP/OTP remains.
    server = make_server("127.0.0.1", 0, gateway.app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(module, "SMS_GATEWAY_URL", f"http://127.0.0.1:{server.server_port}")
    people = [{"mobile": "9000000000", "name": "Synthetic Owner", "bank": "000011110001", "password": "13121950"},
              {"mobile": "9000000001", "name": "Synthetic Other", "bank": "000022220002", "password": "14111949"}]
    ctx = SimpleNamespace(portal=module, database=database, gateway=gateway, people=people, clock=clock,
                          staff_name=staff_name, staff_password=staff_password)
    for index, person in enumerate(people):
        rows = query(ctx, """INSERT INTO applications
            (application_no,applicant_name,mobile,dob,gender,marital_status,village,block,bank_account,ifsc,status,submitted_at)
            VALUES (%s,%s,%s,%s,'Male','Unmarried','Synthetic Village','Sonari',%s,'TEST0000001','PENDING','2026-10-01 12:00:00') RETURNING id""",
            ("SYNTH-" + str(index), person["name"], person["mobile"], "1950-12-13" if index == 0 else "1949-11-14", person["bank"]))
        person["id"] = rows[0][0]
        query(ctx, "INSERT INTO portal_users (mobile,password_hash) VALUES (%s,%s)",
              (person["mobile"], hashlib.sha256(person["password"].encode()).hexdigest()))
    try:
        yield ctx
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
