"""Private replay worker importing unchanged runtime; never used by production entrypoints."""
import configparser
import importlib.util
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

root = Path(__file__).resolve().parents[1]
socket = Path(os.environ["REPLAY_PG_SOCKET"]).resolve(strict=True)
assert socket.name == "socket" and socket.parent.name.startswith("sewasetu-pg-")
assert socket.stat().st_uid == os.getuid() and socket.stat().st_mode & 0o077 == 0
spec = importlib.util.spec_from_file_location("replay_target", root / "app/app.py")
portal = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = portal
spec.loader.exec_module(portal)
if isinstance(getattr(portal, "config", None), configparser.ConfigParser):
    for name, value in {"host": str(socket), "port": "5432", "name": "postgres", "user": "sewasetu_test", "password": "unused-private-test"}.items():
        portal.config.set("database", name, value)
portal.UPLOAD_DIR = os.environ["REPLAY_UPLOAD_DIR"]
portal.SMS_GATEWAY_URL = os.environ["REPLAY_SMS_URL"]
portal.app.secret_key = os.environ["REPLAY_SECRET"]

# Reproducible pre-deadline test date only; all actual authentication/form checks remain.
class ReplayDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        instant = datetime(2026, 10, 24, 12, tzinfo=ZoneInfo("Asia/Kolkata")) + timedelta(seconds=time.time()-float(os.environ["REPLAY_CLOCK_EPOCH"]))
        return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

class ReplayDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 24)

portal.datetime, portal.date = ReplayDateTime, ReplayDate
app = portal.app
