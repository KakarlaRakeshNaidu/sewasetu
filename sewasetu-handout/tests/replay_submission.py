"""Owned restored-copy replay. No external target URL; metrics contain no citizen rows."""
import argparse
import hashlib
import io
import json
import os
import platform
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from werkzeug.security import generate_password_hash
from werkzeug.serving import WSGIRequestHandler, make_server

from pg_runtime import PostgresSandbox
from restore_baseline import source_tables, digest, reconcile, percentiles
from submission_support import HttpClient, accepted, login, prepare, rows, text

ROOT = Path(__file__).resolve().parents[1]


def query(db, statement, params=()):
    with closing(db.connect()) as connection, connection:
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            return cursor.fetchall() if cursor.description else None


def original_rows_unchanged(db, source, synthetic, max_id):
    with closing(db.connect(options="-c default_transaction_read_only=on")) as connection:
        with connection.cursor() as cursor:
            for table, (columns, expected) in source.items():
                condition = cursor.mogrify("id <= %s", (max_id,)).decode() if table == "applications" else cursor.mogrify("NOT (mobile=ANY(%s))", (synthetic,)).decode()
                output = io.StringIO()
                cursor.copy_expert("COPY (SELECT " + ",".join(columns) + " FROM " + table + " WHERE " + condition + ") TO STDOUT", output)
                actual = output.getvalue().removesuffix("\n").split("\n")
                assert len(actual) == len(expected) and digest(actual) == digest(expected), "Original rows changed during replay"


def plans(db, mobile):
    output = {}
    def shape(node):
        result = {key: node[key] for key in ("Node Type", "Actual Rows", "Rows Removed by Filter", "Shared Hit Blocks", "Shared Read Blocks") if key in node}
        result["children"] = [shape(child) for child in node.get("Plans", [])]
        return result
    for name, statement, params in [
        ("mobile_lookup", "SELECT id FROM applications WHERE mobile=%s ORDER BY submitted_at DESC LIMIT 1", (mobile,)),
        ("pending_list", "SELECT id FROM applications WHERE status=%s ORDER BY submitted_at LIMIT 50", ("PENDING",))]:
        value = query(db, "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + statement, params)[0][0][0]
        output[name] = {"execution_ms": value["Execution Time"], "plan": shape(value["Plan"])}
    return output


def resources(master_pid):
    try:
        children = [int(value) for value in Path(f"/proc/{master_pid}/task/{master_pid}/children").read_text().split()]
    except OSError:
        children = []
    ticks, rss = 0, 0
    for pid in [master_pid] + children:
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()
            ticks += int(fields[11]) + int(fields[12])
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    rss += int(line.split()[1])
        except OSError:
            pass
    return ticks / os.sysconf("SC_CLK_TCK"), rss


class Sampler:
    def __init__(self, db, pid):
        self.db, self.pid = db, pid
        self.stop = threading.Event()
        self.peaks = dict(db_connections=0, active=0, idle_in_transaction=0, waiting_locks=0, rss_sum_kib=0)
        self.errors = 0
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        with closing(self.db.connect(options="-c statement_timeout=1000")) as connection:
            connection.autocommit = True
            with connection.cursor() as cursor:
                while not self.stop.is_set():
                    try:
                        cursor.execute("""SELECT count(*),count(*) FILTER(WHERE state='active'),
                            count(*) FILTER(WHERE state LIKE 'idle in transaction%')
                            FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()""")
                        counts = cursor.fetchone()
                        cursor.execute("SELECT count(*) FROM pg_locks WHERE NOT granted AND pid IN (SELECT pid FROM pg_stat_activity WHERE datname=current_database())")
                        lock_count = cursor.fetchone()[0]
                        for key, value in zip(("db_connections", "active", "idle_in_transaction"), counts):
                            self.peaks[key] = max(self.peaks[key], value)
                        self.peaks["waiting_locks"] = max(self.peaks["waiting_locks"], lock_count)
                        self.peaks["rss_sum_kib"] = max(self.peaks["rss_sum_kib"], resources(self.pid)[1])
                    except Exception:
                        self.errors += 1
                    self.stop.wait(.025)


def phase(db, master, name, events, tasks, factor, fanout, tracker, tracker_id, tracker_number):
    results = []
    begin = time.monotonic()
    cpu_begin = resources(master.pid)[0]
    sampler = Sampler(db, master.pid)
    sampler.thread.start()
    def operation(event, task):
        started = time.perf_counter()
        client = task[0].clone() if event["kind"] == "submit" else tracker.clone()
        try:
            response = client.post("/declaration", data=task[1], follow_redirects=True) if event["kind"] == "submit" else client.get(f"/application/{tracker_id}", follow_redirects=True)
            return {"kind": event["kind"], "mobile": task[2] if task else None, "status": response.status_code,
                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                    "body": text(response), "http_requests": 1 + len(response.history)}
        except Exception as error:
            return {"kind": event["kind"], "mobile": task[2] if task else None, "status": 0,
                    "elapsed_ms": (time.perf_counter() - started) * 1000, "body": "", "error": type(error).__name__, "http_requests": 1}
        finally:
            client.close()
    try:
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = []
            for index, event in enumerate(events):
                delay = begin + event["at"] / factor - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                for _ in range(fanout):
                    futures.append(pool.submit(operation, event, tasks.get(index)))
            for future in futures:
                results.append(future.result())
    finally:
        sampler.stop.set()
        sampler.thread.join(timeout=3)
    wall = time.monotonic() - begin
    cpu = max(0, resources(master.pid)[0] - cpu_begin)
    by_contact = {task[2]: rows(db, task[2]) for task in tasks.values()}
    reference = tracker_number
    for result in results:
        found = by_contact.get(result["mobile"], [])
        result["complete"] = result["status"] == 200 and ((len(found) == 1 and found[0][1] in result["body"]) if result["kind"] == "submit" else reference in result["body"])
    stats = {}
    for kind in ("submit", "detail"):
        subset = [result for result in results if result["kind"] == kind]
        stats[kind] = percentiles([result["elapsed_ms"] for result in subset]) | {
            "correct_completions": sum(result["complete"] for result in subset),
            "http_statuses": dict(Counter(str(result["status"]) for result in subset)),
            "transport_errors": dict(Counter(result["error"] for result in subset if "error" in result))}
    bad = sum(not result["complete"] for result in results)
    report = {"phase": name, "compression": factor, "retry_fanout": fanout, "operations": len(results),
              "http_requests_with_redirects": sum(result["http_requests"] for result in results),
              "wall_seconds": round(wall, 3), "operations_per_second": round(len(results) / wall, 3),
              "cpu_seconds_master_workers": round(cpu, 3), "sampled_peaks": sampler.peaks,
              "sampler_errors": sampler.errors, "requests": stats, "incorrect_completions": bad,
              "distinct_submission_intents": len(by_contact), "persisted_applications": sum(len(value) for value in by_contact.values()),
              "duplicate_intents": sum(len(value) > 1 for value in by_contact.values()),
              "missing_intents": sum(not value for value in by_contact.values())}
    print(json.dumps({"phase_result": report}), flush=True)
    return report


class QuietAccess(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass  # No mobile-bearing inbox URLs in output; response/error counts are recorded separately.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    raw = args.seed.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == args.sha256
    source = source_tables(raw.decode("utf-8"))
    scenario_bytes = (ROOT / "tests/submission_scenario.json").read_bytes()
    scenario = json.loads(scenario_bytes)
    events = scenario["events"]
    assert len(events) == 13 and all(0 <= event["at"] < 60 and event["kind"] in ("submit", "detail") for event in events)
    with PostgresSandbox() as db, tempfile.TemporaryDirectory(prefix="sewasetu-replay-") as private:
        work = Path(private)
        db.load(ROOT / "app/schema.sql")
        db.load(args.seed.resolve())
        for migration in sorted((ROOT/"app/migrations").glob("*.sql")):
            db.load(migration)
        original = reconcile(db, source)
        assert original["applications"]["rows"] == 100002
        query(db, "ANALYZE applications,portal_users,otps")
        existing = {row[0] for row in query(db, "SELECT mobile FROM portal_users")}
        original_max_id = max(int(line.split("\t")[0]) for line in source["applications"][1])
        synthetic, templates = [], []
        def contact():
            value = 8000000000 + len(synthetic)
            while str(value) in existing or str(value) in synthetic:
                value += 1
            synthetic.append(str(value))
            return str(value)
        import importlib.util
        spec = importlib.util.spec_from_file_location("replay_gateway", ROOT / "smsgw/app.py")
        gateway = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = gateway
        spec.loader.exec_module(gateway)
        server = make_server("127.0.0.1", 0, gateway.app, request_handler=QuietAccess)
        gateway_thread = threading.Thread(target=server.serve_forever, daemon=True)
        gateway_thread.start()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        base = "http://127.0.0.1:" + str(listener.getsockname()[1])
        secret = secrets.token_hex(32)
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", APP_ENV="test", SECRET_KEY=secret,
            OTP_HMAC_KEY=secrets.token_hex(32), ADMIN_USERNAME="synthetic-staff",
            ADMIN_PASSWORD_HASH=generate_password_hash("Synthetic-Only-Staff-Password"), PUBLIC_BASE_URL=base,
            SESSION_COOKIE_SECURE="false", APP_TIMEZONE="Asia/Kolkata", SCHEME_DEADLINE="2026-11-01T00:00:00+05:30",
            DATABASE_URL="postgresql+psycopg2://sewasetu_test@/postgres?host="+quote(str(db.socket), safe=""),
            SMS_GATEWAY_URL=f"http://127.0.0.1:{server.server_port}", UPLOAD_DIR=str(work/"uploads"),
            REPLAY_PG_SOCKET=str(db.socket), REPLAY_SMS_URL=f"http://127.0.0.1:{server.server_port}",
            REPLAY_UPLOAD_DIR=str(work/"uploads"), REPLAY_SECRET=secret, REPLAY_CLOCK_EPOCH=str(time.time()))
        environment.pop("GUNICORN_CMD_ARGS", None)
        log = (work / "server.log").open("w+")
        master = subprocess.Popen([str(Path(sys.executable).parent / "gunicorn"), "--chdir", str(ROOT / "tests"),
            "--bind", "fd://"+str(listener.fileno()), "--workers", "4", "--worker-class", "sync", "--timeout", "30", "replay_portal:app"],
            env=environment, pass_fds=(listener.fileno(),), stdout=log, stderr=log, start_new_session=True)
        listener.close()
        try:
            probe = HttpClient(base)
            for _ in range(80):
                assert master.poll() is None, "Replay server exited during startup"
                try:
                    if probe.get("/").status_code == 200:
                        break
                except Exception:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError("Private replay server not ready")
            probe.close()
            warm_start = time.monotonic()
            tracker_mobile = contact()
            tracker = HttpClient(base)
            templates.append(tracker)
            data = prepare(tracker, tracker_mobile)
            response = tracker.post("/declaration", data=data, follow_redirects=True)
            tracker_row = rows(db, tracker_mobile)
            assert len(tracker_row) == 1 and accepted(response, tracker_row[0][1])
            tracker_id = tracker_row[0][0]
            phase_tasks = []
            for name, fanout in [("baseline", 1), ("burst-retries", scenario["burst_retry_fanout"]), ("recovery", 1)]:
                tasks = {}
                for index, event in enumerate(events):
                    if event["kind"] == "submit":
                        mobile = contact()
                        browser = HttpClient(base)
                        templates.append(browser)
                        tasks[index] = (browser, prepare(browser, mobile), mobile)
                phase_tasks.append((name, fanout, tasks))
            # A fresh tracking browser must complete its own current OTP, not inherit apply authorization.
            # Respect the real contact send cooldown; this wait is preparation, outside measured requests.
            wait=max(0,61-(time.monotonic()-warm_start))
            if wait:
                time.sleep(wait)
            tracker.close()
            tracker=HttpClient(base)
            templates.append(tracker)
            tracked=login(tracker,tracker_mobile)
            assert tracked.status_code in (302,303), 'Fresh tracking OTP login failed'
            assert tracker.get(f"/application/{tracker_id}").status_code == 200
            preparation_seconds = time.monotonic() - warm_start
            print(json.dumps({"prepared": {"synthetic_contacts": len(synthetic), "seconds": round(preparation_seconds, 3),
                "auth": "Actual captcha, delivered OTP, forms/upload; no injected logged-in state", "carrier_delay_seconds": [gateway.DELAY_MIN, gateway.DELAY_MAX]}}), flush=True)
            before_plans = plans(db, tracker_mobile)
            reports = [phase(db, master, name, events, tasks, scenario["compression"], fanout, tracker, tracker_id, tracker_row[0][1])
                       for name, fanout, tasks in phase_tasks]
            after_plans = plans(db, tracker_mobile)
            original_rows_unchanged(db, source, synthetic, original_max_id)
            duplicates = query(db, "SELECT count(*) FROM (SELECT application_no FROM applications GROUP BY application_no HAVING count(*)>1) d")[0][0]
            sequence = query(db, "SELECT last_value,is_called FROM applications_id_seq")[0]
            log.flush()
            logs = (work / "server.log").read_text()
            source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
            correct = not any(report["incorrect_completions"] or report["duplicate_intents"] or report["missing_intents"] for report in reports) and duplicates == 0
            candidates=subprocess.check_output(['git','ls-files','--full-name','--cached','--others','--exclude-standard','-z','--','app','smsgw'],cwd=ROOT).decode().strip('\0').split('\0')
            manifest=''.join(name+' '+hashlib.sha256((ROOT.parent/name).read_bytes()).hexdigest()+'\n' for name in sorted(set(candidates)) if name)
            source_digest=hashlib.sha256(manifest.encode()).hexdigest()
            idle_after=query(db,"SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND state LIKE %s",("idle in transaction%",))[0][0]
            summary = {"source_commit": source_commit,"runtime_source_sha256":source_digest,"idle_in_transaction_after":idle_after, "python": platform.python_version(), "postgres": db.version,
                "hardware": {"cpus": os.cpu_count(), "memory_bytes": os.sysconf("SC_PAGE_SIZE")*os.sysconf("SC_PHYS_PAGES"), "kernel": platform.release()},
                "workers": 4, "worker_class": "sync", "db_max_connections": 30, "shared_buffers_mib": 32,
                "scenario_sha256": hashlib.sha256(scenario_bytes).hexdigest(), "scenario": scenario,
                "original_reconciliation": original, "original_rows_unchanged": True,
                "preparation_seconds": round(preparation_seconds, 3), "phases": reports,
                "query_plans_before": before_plans, "query_plans_after": after_plans,
                "duplicate_public_reference_groups": duplicates, "sequence_after": sequence,
                "server_error_counts": {"tracebacks": logs.count("Traceback (most recent call last)"), "unique_violations": logs.count("UniqueViolation"), "connection_exhaustion": logs.count("too many clients already")},
                "correctness_passed": correct,
                "limits": "13 selected operations from 159 source requests; 12x time compression; burst adds 4 retries per operation. Local synthetic stress, not full production traffic/capacity proof. RSS sum includes shared pages; preparation/auth timings separate. No latency thresholds decide correctness."}
            print(json.dumps({"replay_summary": summary}), flush=True)
            return 0 if correct else 1
        finally:
            if master.poll() is None:
                os.killpg(master.pid, signal.SIGTERM)
            try:
                master.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(master.pid, signal.SIGKILL)
                master.wait(timeout=5)
            for browser in templates:
                browser.close()
            server.shutdown()
            gateway_thread.join(timeout=5)
            server.server_close()
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
