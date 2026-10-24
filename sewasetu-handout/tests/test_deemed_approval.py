"""Statutory red regressions: frozen aware time, real entry/config, real PG races."""
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from werkzeug.serving import make_server

from conftest import ROOT, load_module


INSTANT = datetime.fromisoformat("2026-10-24T02:00:00+05:30")


def sql(ctx, statement, params=()):
    with closing(ctx.database.connect()) as connection, connection:
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            return cursor.fetchall() if cursor.description else None


def snapshot(ctx):
    return {app_id: data for app_id, data in sql(ctx, "SELECT id,to_jsonb(a) FROM applications a ORDER BY id")}


@pytest.fixture
def sla(database):
    with closing(database.connect()) as connection, connection:
        with connection.cursor() as cursor:
            cursor.execute("TRUNCATE applications, portal_users, otps RESTART IDENTITY")
    gateway = load_module("sla_test_gateway", ROOT / "smsgw/app.py")
    gateway.DELAY_MIN = gateway.DELAY_MAX = 0
    server = make_server("127.0.0.1", 0, gateway.app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ctx = SimpleNamespace(database=database, cases={}, gateway=gateway,
                          gateway_url=f"http://127.0.0.1:{server.server_port}", processes=[])
    cutoff = INSTANT - timedelta(days=15)
    cases = [
        ("overdue", cutoff - timedelta(microseconds=1), "PENDING", None),
        ("exact", cutoff, "PENDING", None),
        ("recent", cutoff + timedelta(microseconds=1), "PENDING", None),
        ("approved", cutoff - timedelta(days=10), "APPROVED", "SYNTHETIC-CASEWORKER"),
        ("rejected", cutoff - timedelta(days=10), "REJECTED", "SYNTHETIC-CASEWORKER"),
        ("deemed", cutoff - timedelta(days=10), "DEEMED_APPROVED", "RTPS-AUTO"),
    ]
    for index, (name, submitted, status, actor) in enumerate(cases):
        decided = (INSTANT - timedelta(days=1)).replace(tzinfo=None) if actor else None
        mobile = str(9100000000 + index)
        rows = sql(ctx, """INSERT INTO applications
            (application_no,applicant_name,mobile,dob,gender,marital_status,village,block,
             bank_account,ifsc,doc_path,status,submitted_at,decided_at,decided_by)
            VALUES (%s,%s,%s,'1950-12-13','Male','Unmarried','Synthetic Village','Sonari',
                    '000012345678','TEST0000001','',%s,%s,%s,%s) RETURNING id""",
            ("SYNTH-SLA-" + str(index), "Synthetic SLA Case " + str(index), mobile, status,
             submitted.replace(tzinfo=None), decided, actor))
        ctx.cases[name] = rows[0][0]
        sql(ctx, "INSERT INTO portal_users (mobile,password_hash) VALUES (%s,%s)",
            (mobile, hashlib.sha256(b"Synthetic account preservation fixture").hexdigest()))
    try:
        yield ctx
    finally:
        for process, _ready in ctx.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def start(ctx, *, configured=True, server_zone="Asia/Kolkata"):
    ready = ctx.database.root / ("job-ready-" + str(len(ctx.processes)) + ".txt")
    args = [sys.executable, "-B", str(ROOT / "tests/deemed_entry_runner.py"),
            "--socket", str(ctx.database.socket), "--instant", INSTANT.isoformat(),
            "--server-zone", server_zone, "--gateway", ctx.gateway_url, "--ready", str(ready)]
    if configured:
        args.append("--legacy-config")
    process = subprocess.Popen(args, cwd=ROOT, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    handle = (process, ready)
    ctx.processes.append(handle)
    return handle


def finish(handle):
    process, _ready = handle
    stdout, _stderr = process.communicate(timeout=15)
    assert process.returncode == 0, "Test runner failed before job outcome could be inspected"
    return json.loads(stdout)


def invoke(ctx, **options):
    return finish(start(ctx, **options))


def test_real_entry_processes_overdue_with_shipped_configuration(sla):
    result = invoke(sla, configured=False)
    status = snapshot(sla)[sla.cases["overdue"]]["status"]
    assert status == "DEEMED_APPROVED", (
        "Real entry left an overdue pending case unprocessed; supporting entry error: " + str(result["error_kind"]))
    assert result["entry_exit"] == 0


@pytest.mark.parametrize("case,expected", [("overdue", "DEEMED_APPROVED"), ("exact", "DEEMED_APPROVED"), ("recent", "PENDING")])
def test_statutory_transition_before_at_after_exact_sla(sla, case, expected):
    result = invoke(sla)
    assert result["entry_exit"] == 0, "Configured job did not complete; inspect setup separately"
    status = snapshot(sla)[sla.cases[case]]["status"]
    assert status == expected, "Statutory decision did not honor the exact 15-day cutoff"
    assert result["entry_exit"] == 0


def test_recent_and_existing_decisions_remain_untouched(sla):
    before = snapshot(sla)
    accounts = sql(sla, "SELECT mobile,password_hash FROM portal_users ORDER BY mobile")
    sequence = sql(sla, "SELECT last_value,is_called FROM applications_id_seq")
    assert invoke(sla)["entry_exit"] == 0
    after = snapshot(sla)
    for case in ["recent", "approved", "rejected", "deemed"]:
        app_id = sla.cases[case]
        assert after[app_id] == before[app_id], "Job overwrote a recent or already-decided case"
    assert sql(sla, "SELECT mobile,password_hash FROM portal_users ORDER BY mobile") == accounts
    assert sql(sla, "SELECT last_value,is_called FROM applications_id_seq") == sequence
    immutable = lambda row: {key: value for key, value in row.items() if key not in ("status", "decided_at", "decided_by")}
    assert {key: immutable(row) for key, row in before.items()} == {key: immutable(row) for key, row in after.items()}


def test_auto_decision_records_frozen_time_and_actor(sla):
    assert invoke(sla)["entry_exit"] == 0
    row = snapshot(sla)[sla.cases["overdue"]]
    assert row["status"] == "DEEMED_APPROVED"
    assert datetime.fromisoformat(row["decided_at"]) == INSTANT.replace(tzinfo=None)
    assert row["decided_by"] == "RTPS-AUTO"


def test_rerun_does_not_overwrite_existing_decision_fields(sla):
    assert invoke(sla)["entry_exit"] == 0
    first = snapshot(sla)
    assert invoke(sla)["entry_exit"] == 0
    assert snapshot(sla) == first, "Rerun changed an already-processed decision"


def test_inherited_naive_submission_time_is_interpreted_as_ist_on_utc_server(sla):
    result = invoke(sla, server_zone="UTC")
    assert result["entry_exit"] == 0, "Configured job did not complete; inspect setup separately"
    status = snapshot(sla)[sla.cases["overdue"]]["status"]
    assert status == "DEEMED_APPROVED", "UTC host missed a case whose inherited IST timestamp exceeded its SLA"
    assert result["entry_exit"] == 0


def wait_for_job_row_lock(ctx, handle):
    process, ready = handle
    deadline = time.monotonic() + 8
    with closing(ctx.database.connect()) as observer:
        observer.autocommit = True  # Refresh pg_stat_activity on each poll, not one cached transaction.
        with observer.cursor() as cursor:
            while time.monotonic() < deadline:
                pids = [int(line) for line in ready.read_text().splitlines()] if ready.exists() else []
                if pids:
                    cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=ANY(%s) AND wait_event_type='Lock')", (pids,))
                    if cursor.fetchone()[0]:
                        return
                if process.poll() is not None:
                    break
                time.sleep(.02)
    pytest.fail("Could not establish the real job/manual-decision lock race")


@pytest.mark.parametrize("manual_status", ["APPROVED", "REJECTED"])
def test_committed_manual_decision_wins_real_job_row_lock_race(sla, manual_status):
    target = sla.cases["overdue"]
    with closing(sla.database.connect()) as manual:
        try:
            with manual.cursor() as cursor:
                cursor.execute("UPDATE applications SET status=%s,decided_at=%s,decided_by=%s WHERE id=%s",
                               (manual_status, INSTANT.replace(tzinfo=None), "SYNTHETIC-RACING-STAFF", target))
            handle = start(sla)
            wait_for_job_row_lock(sla, handle)
            manual.commit()  # Release only after observing the competing real UPDATE blocked in PostgreSQL.
            result = finish(handle)
        finally:
            manual.rollback()
    row = snapshot(sla)[target]
    assert row["status"] == manual_status and row["decided_by"] == "SYNTHETIC-RACING-STAFF", "Automatic job overwrote the winning manual decision"
    assert row["decided_at"] == INSTANT.replace(tzinfo=None).isoformat()
    assert result["entry_exit"] == 0
