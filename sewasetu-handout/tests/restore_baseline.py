"""Restore supplied data ONLY into an owned temporary cluster; print aggregate evidence."""
import argparse
import hashlib
import importlib.util
import io
import json
import math
import os
import secrets
import re
import statistics
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

from pg_runtime import PostgresSandbox

ROOT = Path(__file__).resolve().parents[1]


def source_tables(text):
    tables = {}
    current = None
    for line in text.split("\n"):
        if line.startswith("COPY "):
            match = re.fullmatch(r"COPY (applications|portal_users) \(([^)]+)\) FROM stdin;", line)
            if not match:
                raise ValueError("Unexpected COPY table in supplied seed")
            current = match[1]
            columns = [c.strip() for c in match[2].split(",")]
            assert all(re.fullmatch(r"[a-z_]+", c) for c in columns)
            tables[current] = (columns, [])
        elif line == "\\.":
            current = None
        elif current:
            assert len(line.split("\t")) == len(tables[current][0])
            tables[current][1].append(line)
    assert set(tables) == {"applications", "portal_users"}
    return tables


def digest(lines):
    return hashlib.sha256("".join(line + "\n" for line in sorted(lines)).encode()).hexdigest()


def reconcile(db, source):
    result = {}
    with closing(db.connect(options="-c default_transaction_read_only=on")) as conn:
        with conn.cursor() as cur:
            for table, (columns, expected) in source.items():
                data = io.StringIO()
                cur.copy_expert("COPY (SELECT " + ",".join(columns) + " FROM " + table + ") TO STDOUT", data)
                actual = data.getvalue().removesuffix("\n").split("\n")
                assert len(actual) == len(expected), "Restored row count mismatch"
                assert digest(actual) == digest(expected), "Restored COPY values differ from source"
                result[table] = {"rows": len(actual), "copy_rows_sha256": digest(actual)}
            cur.execute("SELECT last_value, is_called FROM applications_id_seq")
            sequence = cur.fetchone()
            assert sequence == (100012, True)
            result["sequence"] = {"last_value": sequence[0], "is_called": sequence[1]}
            cur.execute("SELECT status, count(*) FROM applications GROUP BY status ORDER BY status")
            result["statuses"] = dict(cur.fetchall())
    return result


def percentiles(values):
    ordered = sorted(values)
    return {"samples": len(values), "p50_ms": round(statistics.median(values), 3),
            "p95_ms": round(ordered[math.ceil(len(values) * .95) - 1], 3),
            "p99_ms": round(ordered[math.ceil(len(values) * .99) - 1], 3)}


def profile(db, upload_dir):
    # Private fixture configuration only; never reuse operator or live credentials.
    from werkzeug.security import generate_password_hash
    overrides={'APP_ENV':'development','PUBLIC_BASE_URL':'http://localhost','SECRET_KEY':secrets.token_urlsafe(48),
        'OTP_HMAC_KEY':secrets.token_urlsafe(48),'DATABASE_URL':'postgresql+psycopg2://profile:private-fixture@localhost/profile',
        'ADMIN_USERNAME':'profile-staff','ADMIN_PASSWORD_HASH':generate_password_hash('Synthetic-Only-Profile-Password'),
        'SESSION_COOKIE_SECURE':'false','UPLOAD_DIR':str(upload_dir)}
    os.environ.update(overrides)
    spec = importlib.util.spec_from_file_location("profile_portal", ROOT / "app/app.py")
    portal = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = portal
    spec.loader.exec_module(portal)
    portal.get_db = db.connect
    portal.UPLOAD_DIR = upload_dir
    portal.app.config.update(TESTING=True, SECRET_KEY="temporary-profile-key")
    with closing(portal.get_db()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, mobile FROM applications ORDER BY id LIMIT 1")
            app_id, mobile = cur.fetchone()
    client = portal.app.test_client()
    with client.session_transaction() as session:
        session.update(logged_in=True, role="citizen", portal_mobile=mobile)
    timings = {"status_detail": [], "uncached_pdf": []}
    for _ in range(20):
        start = time.perf_counter()
        response = client.get(f"/application/{app_id}")
        assert response.status_code == 200
        timings["status_detail"].append((time.perf_counter() - start) * 1000)
        # Remove only this profiler's generated cache, so every sample renders.
        for cached in (Path(upload_dir)/'ack').glob('*.pdf'):
            cached.unlink()
        start = time.perf_counter()
        response = client.get(f"/ack/{app_id}.pdf")
        assert response.status_code == 200 and response.data.startswith(b"%PDF-")
        timings["uncached_pdf"].append((time.perf_counter() - start) * 1000)
    plans = {}
    with closing(portal.get_db()) as conn:
        with conn.cursor() as cur:
            for name, sql, args in [
                ("status_lookup", "SELECT id FROM applications WHERE mobile=%s ORDER BY submitted_at DESC LIMIT 1", (mobile,)),
                ("pending_list", "SELECT id FROM applications WHERE status=%s ORDER BY submitted_at LIMIT 50", ("PENDING",)),
            ]:
                cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, args)
                plan = cur.fetchone()[0][0]
                def compact(node):
                    return {key: node[key] for key in ["Node Type", "Actual Rows", "Rows Removed by Filter", "Shared Hit Blocks", "Shared Read Blocks"] if key in node} | {"children": [compact(n) for n in node.get("Plans", [])]}
                plans[name] = {"execution_ms": plan["Execution Time"], "plan": compact(plan["Plan"])}
    # No predicates/rows/responses/PDFs are printed: they contain original citizen data.
    return {"http": {k: percentiles(v) for k, v in timings.items()}, "query_plans": plans}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    raw = args.seed.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == args.sha256, "Seed checksum mismatch"
    source = source_tables(raw.decode("utf-8"))
    with PostgresSandbox() as db:
        # New initdb cluster only; no external database URL or scheduler is accepted.
        db.load(ROOT / "app/schema.sql")
        db.load(args.seed.resolve())
        for migration in sorted((ROOT/'app/migrations').glob('*.sql')):db.load(migration)
        before = reconcile(db, source)
        with tempfile.TemporaryDirectory(prefix="sewasetu-profile-") as uploads:
            measurements = profile(db, uploads)
        after = reconcile(db, source)
        assert before == after, "Restored baseline changed during read-only profiling"
        print(json.dumps({"postgres": db.version, "reconciled": before,
                          "measurements": measurements, "unchanged_after_profile": True}, indent=2))


if __name__ == "__main__":
    main()
