"""Validated shared settings and bounded process-local PostgreSQL pool."""
import os
from datetime import datetime
from urllib.parse import urlparse
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def settings():
    e = os.environ
    mode = e.get("APP_ENV", "production")
    if mode not in ("production", "development", "test"):
        raise RuntimeError("Invalid APP_ENV")
    names = ("SECRET_KEY", "OTP_HMAC_KEY", "DATABASE_URL", "ADMIN_USERNAME", "ADMIN_PASSWORD_HASH", "PUBLIC_BASE_URL")
    if any(not e.get(k) for k in names):
        raise RuntimeError("Required private runtime configuration is missing")
    if min(len(e["SECRET_KEY"]), len(e["OTP_HMAC_KEY"])) < 32 or e["SECRET_KEY"] == e["OTP_HMAC_KEY"] or "ssp-prod-key" in e["SECRET_KEY"]:
        raise RuntimeError("Use independent newly generated signing and OTP secrets")
    if e["ADMIN_USERNAME"] == "admin" or not e["ADMIN_PASSWORD_HASH"].startswith(("scrypt:", "pbkdf2:")):
        raise RuntimeError("Configure new staff credentials using a modern password hash")
    db = make_url(e["DATABASE_URL"])
    if db.get_backend_name() != "postgresql" or (mode == "production" and (not db.password or db.password in ("ssp_pass", "password", "sewasetu", "sewasetu123"))):
        raise RuntimeError("Configure PostgreSQL with a new private runtime password")
    public = urlparse(e["PUBLIC_BASE_URL"])
    secure = e.get("SESSION_COOKIE_SECURE", "true").lower() == "true"
    if not public.hostname or public.scheme not in ("http", "https") or (mode == "production" and (public.scheme != "https" or not secure)):
        raise RuntimeError("Production requires HTTPS and secure cookies")
    deadline = datetime.fromisoformat(e.get("SCHEME_DEADLINE", "2026-11-01T00:00:00+05:30"))
    if deadline.utcoffset() is None:
        raise RuntimeError("Deadline must include its timezone")
    def number(name, default, low, high):
        value = int(e.get(name, default))
        if not low <= value <= high:
            raise RuntimeError("Invalid bounded setting: " + name)
        return value
    return dict(mode=mode, secret=e["SECRET_KEY"], otp_key=e["OTP_HMAC_KEY"], database_url=e["DATABASE_URL"],
        admin=e["ADMIN_USERNAME"], admin_hash=e["ADMIN_PASSWORD_HASH"], secure=secure,
        public_origin=public.scheme+"://"+public.netloc, deadline=deadline,
        pool_size=number("DB_POOL_SIZE",3,1,10), pool_overflow=number("DB_POOL_OVERFLOW",1,0,5),
        otp_seconds=number("OTP_VALIDITY_SECONDS",300,60,600), attempts=number("OTP_MAX_ATTEMPTS",5,1,10),
        cooldown=number("OTP_RESEND_COOLDOWN_SECONDS",60,1,300), sends=number("OTP_MAX_SENDS_PER_HOUR",10,1,30),
        idle=number("SESSION_IDLE_SECONDS",1800,600,7200), absolute=number("SESSION_ABSOLUTE_SECONDS",7200,1800,14400))


class Database:
    def __init__(self, config):
        self.config, self.pid, self.engine = config, None, None

    def connect(self):
        if self.pid != os.getpid():
            if self.engine is not None:
                self.engine.dispose(close=False)
            self.engine = create_engine(self.config["database_url"], pool_size=self.config["pool_size"],
                max_overflow=self.config["pool_overflow"], pool_timeout=2, pool_pre_ping=True,
                connect_args={"connect_timeout":5, "options":"-c statement_timeout=5000 -c lock_timeout=2000"})
            self.pid = os.getpid()
        connection = self.engine.raw_connection()
        if self.config["mode"] == "production":
            try:
                with connection.cursor() as cur:
                    cur.execute("SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user")
                    if any(cur.fetchone()):
                        raise RuntimeError("Runtime role must have no administrative privileges")
                connection.rollback()
            except BaseException:
                connection.close()
                raise
        return connection
