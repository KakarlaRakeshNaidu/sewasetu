"""Submission test/replay helpers: genuine OTP/forms; no injected authenticated state."""
import io
import re
import time
from contextlib import closing
from html.parser import HTMLParser

from fpdf import FPDF
import requests


class Inputs(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.fields = {}
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name") and attrs.get("type", "text") != "checkbox":
            self.fields[attrs["name"]] = attrs.get("value", "")


def text(response):
    return response.get_data(as_text=True) if hasattr(response, "get_data") else response.text


def payload(response):
    value = response.json
    return value() if callable(value) else value


def form_fields(response):
    return {key: value for key, value in Inputs(text(response)).fields.items()
            if key in ("csrf_token", "submission_key", "idempotency_key", "draft_id", "version")}


class HttpClient:
    """Small requests adapter with Flask-client-compatible follow_redirects for replay."""
    def __init__(self, base_url, cookies=None):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.trust_env = False  # Private loopback replay must not use a host HTTP proxy.
        if cookies is not None:
            self.session.cookies.update(cookies)

    def get(self, path, follow_redirects=False, **kwargs):
        return self.session.get(self.base_url + path, allow_redirects=follow_redirects, timeout=12, **kwargs)

    def post(self, path, follow_redirects=False, **kwargs):
        return self.session.post(self.base_url + path, allow_redirects=follow_redirects, timeout=12, **kwargs)

    def clone(self):
        return HttpClient(self.base_url, self.session.cookies.copy())

    def close(self):
        self.session.close()


def post_form(client, path, data=None, *, page=None, follow_redirects=False):
    fields = form_fields(client.get(page or path))
    return client.post(path, data=fields | (data or {}), follow_redirects=follow_redirects)


def otp(client, mobile):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get("/__gateway/api/messages", params={"to": mobile}) if isinstance(client, HttpClient) else client.get("/__gateway/api/messages", query_string={"to": mobile})
        assert response.status_code == 200, "Required gateway inbox unavailable"
        for message in reversed(payload(response)["messages"]):
            if message["delivered"] and message["text"]:
                match = re.search(r"\b\d{6}\b", message["text"])
                if match:
                    return match[0]
        time.sleep(.05)
    raise AssertionError("Synthetic verification code was not delivered")


def prepare(client, mobile, name="Synthetic Applicant"):
    response = client.get("/apply")
    question = re.search(r"(\d+)\s*\+\s*(\d+)", text(response))
    assert question, "Expected the actual application captcha"
    data = form_fields(response) | {"mobile": mobile, "captcha": str(int(question[1]) + int(question[2]))}
    assert client.post("/apply", data=data).status_code in (200, 302, 303)
    assert post_form(client, "/verify", {"otp": otp(client, mobile)}).status_code in (200, 302, 303)
    assert client.get("/form/1").status_code == 200
    steps = [
        {"applicant_name": name, "dob": "13/12/1950", "gender": "Male", "marital_status": "Unmarried"},
        {"village": "Synthetic Village", "block": "Sonari"},
        {"bank_account": "000012345678", "ifsc": "TEST0000001"},
    ]
    for step, values in enumerate(steps, 1):
        assert post_form(client, f"/form/{step}", values).status_code in (200, 302, 303)
    proof = FPDF()
    proof.add_page()
    proof.set_font("Helvetica", size=10)
    proof.cell(60, 10, "Synthetic age proof")
    raw = bytes(proof.output())
    fields = form_fields(client.get("/upload"))
    if isinstance(client, HttpClient):
        response = client.post("/upload", data=fields, files={"document": ("proof.pdf", raw, "application/pdf")})
    else:
        response = client.post("/upload", data=fields | {"document": (io.BytesIO(raw), "proof.pdf")})
    assert response.status_code in (302, 303), "Valid synthetic proof was not accepted"
    review = client.get("/declaration")
    assert review.status_code == 200
    return form_fields(review) | {"declaration": "yes"}


def login(client, mobile):
    response = post_form(client, "/status", {"mobile": mobile, "password": "13121950"})
    if "/status/verify" in response.headers.get("Location", "") or "otp" in Inputs(text(response)).fields:
        response = post_form(client, "/status/verify", {"otp": otp(client, mobile)})
    return response


def rows(database, mobile=None):
    with closing(database.connect()) as connection:
        with connection.cursor() as cursor:
            query = "SELECT id,application_no,mobile FROM applications"
            cursor.execute(query + (" WHERE mobile=%s" if mobile else "") + " ORDER BY id", (mobile,) if mobile else ())
            return cursor.fetchall()


def accepted(response, number):
    return response.status_code == 200 and number in text(response)


def clone_client(portal, client):
    clone = portal.app.test_client()
    name = portal.app.config.get("SESSION_COOKIE_NAME", "session")
    cookie = client.get_cookie(name)
    assert cookie is not None
    clone.set_cookie(name, cookie.value)
    return clone
