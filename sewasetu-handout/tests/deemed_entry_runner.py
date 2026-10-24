"""Test-only frozen-clock runner for the real statutory entry point, never cron.

--legacy-config supplies private test input in memory so boundary/race cases can
reach the inherited SQL without creating or repairing app/config/settings.ini.
The separate shipped-config test deliberately does not enable that seam.
"""
import argparse
import builtins
import contextlib
import datetime as datetime_module
import io
import json
import os
import runpy
import secrets
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo
from werkzeug.security import generate_password_hash

import psycopg2
from psycopg2.extensions import parse_dsn
import requests

ROOT = Path(__file__).resolve().parents[1]
RealDateTime = datetime_module.datetime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--instant", required=True)
    parser.add_argument("--server-zone", default="Asia/Kolkata")
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--ready", required=True, type=Path)
    parser.add_argument("--legacy-config", action="store_true")
    args = parser.parse_args()
    socket = args.socket.resolve(strict=True)
    assert socket.name == "socket" and socket.parent.name.startswith("sewasetu-pg-")
    assert socket.stat().st_uid == os.getuid() and (socket.stat().st_mode & 0o077) == 0
    assert (socket / ".s.PGSQL.5432").exists()
    ready = args.ready.resolve()
    assert ready.parent == socket.parent
    instant = RealDateTime.fromisoformat(args.instant)
    assert instant.tzinfo is not None, "Evaluation instant must be aware"
    zone = ZoneInfo(args.server_zone)
    gateway = urlsplit(args.gateway)
    assert gateway.scheme == "http" and gateway.hostname == "127.0.0.1" and gateway.port
    os.environ.update(APP_ENV="test", APP_TIMEZONE="Asia/Kolkata", SLA_DAYS="15", MIN_AGE="60",
                      SECRET_KEY=secrets.token_hex(32), OTP_HMAC_KEY=secrets.token_hex(32),
                      ADMIN_USERNAME="synthetic-staff", ADMIN_PASSWORD_HASH=generate_password_hash("Synthetic-Only-Staff-Password"),
                      PUBLIC_BASE_URL="http://localhost", SCHEME_DEADLINE="2026-11-01T00:00:00+05:30",
                      DATABASE_URL="postgresql+psycopg2://sewasetu_test@/postgres?host=" + quote(str(socket), safe=""),
                      SMS_GATEWAY_URL=args.gateway, SMS_CONNECT_TIMEOUT_SECONDS="2", SMS_READ_TIMEOUT_SECONDS="5",
                      SESSION_COOKIE_SECURE="false", PGHOST=str(socket), PGPORT="5432", PGUSER="sewasetu_test",
                      PGDATABASE="postgres", TZ=args.server_zone)
    os.environ.pop("PGPASSWORD", None)
    time.tzset()
    original_open, original_connect = builtins.open, psycopg2.connect
    original_request = requests.sessions.Session.request
    legacy_path = (ROOT / "app/config/settings.ini").resolve()
    config_reads = []
    ini = ("[database]\nhost = " + str(socket) + "\nport = 5432\nname = postgres\nuser = sewasetu_test\npassword = unused-test-only\n"
           "[app]\nsms_gateway_url = " + args.gateway + "\n[pension]\nsla_days = 15\n")

    def test_open(file, *positional, **keywords):
        if isinstance(file, (str, os.PathLike)) and Path(file).resolve() == legacy_path:
            config_reads.append(True)
            if args.legacy_config:
                return io.StringIO(ini)
        return original_open(file, *positional, **keywords)

    def guarded_connect(*positional, **keywords):
        params = parse_dsn(positional[0]) if positional and positional[0] else {}
        params.update(keywords)
        assert params.get("host", os.environ["PGHOST"]) == str(socket), "External database connection forbidden"
        assert str(params.get("port", "5432")) == "5432"
        assert params.get("dbname", "postgres") == "postgres" and params.get("user", "sewasetu_test") == "sewasetu_test"
        connection = original_connect(*positional, **keywords)
        with original_open(ready, "a", encoding="utf-8") as marker:
            marker.write(str(connection.get_backend_pid()) + "\n")
        return connection

    def guarded_request(session, method, url, *positional, **keywords):
        parsed = urlsplit(url)
        assert parsed.scheme == gateway.scheme and parsed.netloc == gateway.netloc, "External HTTP forbidden"
        assert method.upper() == "POST" and parsed.path == "/api/send"
        return original_request(session, method, url, *positional, **keywords)

    class FrozenDateTime(RealDateTime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.astimezone(zone).replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            return instant.astimezone(datetime_module.timezone.utc).replace(tzinfo=None)

    result = {"entry_exit": 0, "error_kind": None}
    with patch("builtins.open", test_open), patch.object(datetime_module, "datetime", FrozenDateTime), \
            patch("psycopg2.connect", guarded_connect), patch.object(requests.sessions.Session, "request", guarded_request), \
            patch("sys.argv", [str(ROOT / "app/scripts/deemed_approval.py")]), \
            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            runpy.run_path(str(ROOT / "app/scripts/deemed_approval.py"), run_name="__main__")
        except SystemExit as error:
            result["entry_exit"] = int(error.code or 0)
        except Exception as error:
            result.update(entry_exit=1, error_kind=type(error).__name__)
    result["legacy_config_reads"] = len(config_reads)
    print(json.dumps(result))  # No SQL, citizen data, message payload or secret is printed.


if __name__ == "__main__":
    main()
