from contextlib import closing
import importlib.util
import sys
import secrets
from urllib.parse import quote
from werkzeug.security import generate_password_hash
import threading
from datetime import date, datetime
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from pg_runtime import PostgresSandbox

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def database():
    with PostgresSandbox() as db:
        db.load(ROOT / "app/schema.sql")
        for migration in sorted((ROOT / "app/migrations").glob("*.sql")):
            db.load(migration)
        yield db


@pytest.fixture
def portal(database, monkeypatch, tmp_path):
    with closing(database.connect()) as conn, conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE applications, portal_users, otps, web_sessions, portal_challenges, application_drafts, application_audit RESTART IDENTITY CASCADE")
    values = dict(APP_ENV='test', PUBLIC_BASE_URL='http://localhost',
        SECRET_KEY=secrets.token_hex(32),OTP_HMAC_KEY=secrets.token_hex(32),
        DATABASE_URL='postgresql+psycopg2://sewasetu_test@/postgres?host='+quote(database.connection['host'],safe=''),
        ADMIN_USERNAME='synthetic-staff',ADMIN_PASSWORD_HASH=generate_password_hash('Synthetic-Only-Staff-Password'),
        SESSION_COOKIE_SECURE='false')
    for key,value in values.items():
        monkeypatch.setenv(key,value)
    module = load_module("baseline_portal", ROOT / "app/app.py")
    module.app.config.update(TESTING=True)
    monkeypatch.setattr(module, "get_db", database.connect)
    monkeypatch.setattr(module, "UPLOAD_DIR", str(tmp_path / "uploads"))

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 10, 24, 12)
            return value if tz is None else value.replace(tzinfo=tz)

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 24)

    # Reproducible pre-deadline test clock; production policy/clock are unchanged.
    monkeypatch.setattr(module, "datetime", FixedDateTime)
    monkeypatch.setattr(module, "date", FixedDate)
    yield module


@pytest.fixture
def gateway(portal, monkeypatch):
    module = load_module("baseline_gateway", ROOT / "smsgw/app.py")
    server = make_server("127.0.0.1", 0, module.app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(portal, "SMS_GATEWAY_URL", "http://127.0.0.1:" + str(server.server_port))
    try:
        yield module
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
