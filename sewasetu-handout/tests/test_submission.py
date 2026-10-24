"""Correctness regressions for submission faults, retries and concurrent intents."""
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from types import SimpleNamespace

import pytest

from submission_support import accepted, clone_client, prepare, rows


@pytest.fixture
def submission(portal, gateway, database, monkeypatch):
    gateway.DELAY_MIN = gateway.DELAY_MAX = 0  # Controlled carrier latency; real OTP and HTTP remain.
    opened = []
    def connect():
        connection = database.connect()
        opened.append(connection)
        return connection
    monkeypatch.setattr(portal, "get_db", connect)
    try:
        yield SimpleNamespace(portal=portal, gateway=gateway, database=database, mobile="9200000000")
    finally:
        # Retain references for explicit cleanup observations; close only this test's real connections.
        for connection in opened:
            connection.close()


@pytest.mark.parametrize("downstream", ["pdf", "sms"])
def test_downstream_failure_after_commit_preserves_accepted_confirmation(submission, monkeypatch, downstream):
    ctx = submission
    client = ctx.portal.app.test_client()
    data = prepare(client, ctx.mobile)
    ctx.portal.app.config["TESTING"] = False
    def fault(*args, **kwargs):
        raise RuntimeError("Synthetic downstream boundary failure")
    monkeypatch.setattr(ctx.portal, "generate_acknowledgment" if downstream == "pdf" else "send_sms", fault)
    response = client.post("/declaration", data=data, follow_redirects=True)
    persisted = rows(ctx.database, ctx.mobile)
    assert len(persisted) == 1, "A downstream failure changed durable acceptance"
    assert accepted(response, persisted[0][1]), "Committed application was reported as a failed submission"


def test_successful_post_uses_refresh_safe_redirect(submission):
    ctx = submission
    client = ctx.portal.app.test_client()
    data = prepare(client, ctx.mobile)
    response = client.post("/declaration", data=data)
    persisted = rows(ctx.database, ctx.mobile)
    assert len(persisted) == 1
    assert response.status_code in (302, 303), "Success returned a resubmittable POST response instead of PRG"
    assert accepted(client.get(response.headers["Location"]), persisted[0][1])


def test_same_intent_replay_returns_original_application_result(submission):
    ctx = submission
    client = ctx.portal.app.test_client()
    data = prepare(client, ctx.mobile)
    retry = clone_client(ctx.portal, client)  # Model a lost response and replay of the original valid cookie.
    response = client.post("/declaration", data=data, follow_redirects=True)
    first = rows(ctx.database, ctx.mobile)
    assert len(first) == 1 and accepted(response, first[0][1])
    repeated = retry.post("/declaration", data=data, follow_redirects=True)
    assert rows(ctx.database, ctx.mobile) == first, "Retry created another accepted record"
    assert accepted(repeated, first[0][1]), "Retry lost the original accepted result"


def test_concurrent_same_intent_creates_one_record_and_same_result(submission, monkeypatch):
    ctx = submission
    client = ctx.portal.app.test_client()
    data = prepare(client, ctx.mobile)
    second = clone_client(ctx.portal, client)
    # Existing contact account avoids an unrelated portal_users uniqueness race masking duplicate applications.
    original_hash = hashlib.sha256(b"Synthetic existing account password").hexdigest()
    with closing(ctx.database.connect()) as connection, connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO portal_users (mobile,password_hash) VALUES (%s,%s)", (ctx.mobile, original_hash))
    entered, release, second_factory = threading.Event(), threading.Event(), threading.Event()
    lock = threading.Lock()
    counter = 0
    def next_number():
        nonlocal counter
        with lock:
            counter += 1
            index = counter
        if index == 1:
            entered.set()
            assert release.wait(5), "Test failed to release submission interleave"
        else:
            second_factory.set()
        return "SSP26" + str(100000 + index)
    monkeypatch.setattr(ctx.portal, "new_application_no", next_number)
    ctx.portal.app.config["TESTING"] = False
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(client.post, "/declaration", data=data, follow_redirects=True)
        try:
            assert entered.wait(5), "Submission did not reach its number-generation boundary"
            second_future = pool.submit(second.post, "/declaration", data=data, follow_redirects=True)
            # Old code reaches the second factory; a correct serialized implementation may wait on its draft lock.
            second_factory.wait(.3)
        finally:
            release.set()
        responses = [first_future.result(timeout=10), second_future.result(timeout=10)]
    persisted = rows(ctx.database, ctx.mobile)
    assert len(persisted) == 1, "Concurrent requests for one intent created multiple applications"
    assert all(accepted(response, persisted[0][1]) for response in responses), "Concurrent requests disagreed on their result"
    with closing(ctx.database.connect()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT password_hash FROM portal_users WHERE mobile=%s", (ctx.mobile,))
            assert cursor.fetchone()[0] == original_hash


def test_number_collision_is_retried_without_duplicate_reference(submission, monkeypatch):
    ctx = submission
    client = ctx.portal.app.test_client()
    data = prepare(client, ctx.mobile)
    with closing(ctx.database.connect()) as connection, connection:
        with connection.cursor() as cursor:
            cursor.execute("""INSERT INTO applications (application_no,applicant_name,mobile,dob,status,submitted_at)
                VALUES ('SSP26123456','Synthetic Earlier','9200000001','1950-12-13','PENDING','2026-10-01')""")
    numbers = iter(["SSP26123456", "SSP26654321"])
    monkeypatch.setattr(ctx.portal, "new_application_no", lambda: next(numbers))
    ctx.portal.app.config["TESTING"] = False
    response = client.post("/declaration", data=data, follow_redirects=True)
    all_rows = rows(ctx.database)
    references = [row[1] for row in all_rows]
    assert len(references) == len(set(references)), "Application-number collision produced duplicate public references"
    own = rows(ctx.database, ctx.mobile)
    assert len(own) == 1 and accepted(response, own[0][1]), "Collision prevented recoverable acceptance"


def test_real_sms_http_outage_does_not_undo_acceptance(submission, monkeypatch):
    ctx = submission
    client = ctx.portal.app.test_client()
    data = prepare(client, ctx.mobile)
    attempts = []
    def unavailable():
        attempts.append(True)
        return {"status": "error", "reason": "synthetic outage"}, 503
    monkeypatch.setitem(ctx.gateway.app.view_functions, "send", unavailable)
    import requests
    preflight = requests.post(ctx.portal.SMS_GATEWAY_URL + "/api/send",
                              json={"to": "9200000099", "text": "Synthetic outage preflight"}, timeout=2)
    assert preflight.status_code == 503
    response = client.post("/declaration", data=data, follow_redirects=True)
    persisted = rows(ctx.database, ctx.mobile)
    assert len(persisted) == 1 and accepted(response, persisted[0][1])
    # The inherited implementation already swallows this response. This is a preservation guard, not durable-retry proof.
    assert attempts, "Synthetic upstream outage was not armed"


def test_database_failure_rolls_back_partial_application_and_allows_retry(submission):
    ctx = submission
    client = ctx.portal.app.test_client()
    data = prepare(client, ctx.mobile)
    ctx.portal.app.config["TESTING"] = False
    with closing(ctx.database.connect()) as connection, connection:
        with connection.cursor() as cursor:
            cursor.execute("""CREATE FUNCTION synthetic_account_fault() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'synthetic account insert failure'; END $$""")
            cursor.execute("CREATE TRIGGER synthetic_account_fault BEFORE INSERT ON portal_users FOR EACH ROW EXECUTE FUNCTION synthetic_account_fault()")
    response = client.post("/declaration", data=data)
    assert response.status_code in (500, 503)
    assert rows(ctx.database, ctx.mobile) == [], "Failed atomic submission left a partial application"
    with closing(ctx.database.connect()) as connection, connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM portal_users WHERE mobile=%s", (ctx.mobile,))
            assert cursor.fetchone()[0] == 0
            cursor.execute("DROP TRIGGER synthetic_account_fault ON portal_users")
            cursor.execute("DROP FUNCTION synthetic_account_fault()")
    retry = client.post("/declaration", data=data, follow_redirects=True)
    persisted = rows(ctx.database, ctx.mobile)
    assert len(persisted) == 1 and accepted(retry, persisted[0][1])


def test_submission_exception_releases_aborted_database_transactions(submission, monkeypatch):
    ctx = submission
    requests = []
    for index in range(3):
        client = ctx.portal.app.test_client()
        requests.append((client, prepare(client, str(9200000010 + index))))
    with closing(ctx.database.connect()) as connection, connection:
        with connection.cursor() as cursor:
            cursor.execute("""CREATE FUNCTION synthetic_submission_fault() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'synthetic application insert failure'; END $$""")
            cursor.execute("CREATE TRIGGER synthetic_submission_fault BEFORE INSERT ON applications FOR EACH ROW EXECUTE FUNCTION synthetic_submission_fault()")
    opened = []
    def connect():
        connection = ctx.database.connect()
        opened.append(connection)  # Delay GC to observe explicit transaction cleanup.
        return connection
    monkeypatch.setattr(ctx.portal, "get_db", connect)
    ctx.portal.app.config["TESTING"] = False
    try:
        for client, data in requests:
            assert client.post("/declaration", data=data).status_code in (500, 503)
        with closing(ctx.database.connect()) as connection, connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND state LIKE %s", ("idle in transaction%",))
                aborted = cursor.fetchone()[0]
                cursor.execute("DROP TRIGGER synthetic_submission_fault ON applications")
                cursor.execute("DROP FUNCTION synthetic_submission_fault()")
        client, data = requests[0]
        recovery = client.post("/declaration", data=data, follow_redirects=True)
        recovered = rows(ctx.database, "9200000010")
        assert len(recovered) == 1 and accepted(recovery, recovered[0][1])
    finally:
        for connection in opened:
            connection.close()
    assert aborted == 0, "Submission failures retained aborted PostgreSQL transactions"


def test_receipt_render_releases_database_and_cache_tracks_decision(submission,monkeypatch):
    ctx=submission
    client=ctx.portal.app.test_client()
    data=prepare(client,ctx.mobile)
    assert client.post('/declaration',data=data,follow_redirects=True).status_code==200
    app_id=rows(ctx.database,ctx.mobile)[0][0]
    opened=[]
    def connect():
        conn=ctx.database.connect();opened.append(conn);return conn
    monkeypatch.setattr(ctx.portal,'get_db',connect)
    original=ctx.portal.generate_acknowledgment
    rendered=[]
    def detached(cur,ident,row=None):
        assert cur is None and all(conn.closed for conn in opened), 'Renderer retained a PostgreSQL connection'
        rendered.append(row[-1])
        return original(cur,ident,row=row)
    monkeypatch.setattr(ctx.portal,'generate_acknowledgment',detached)
    assert client.get(f'/ack/{app_id}.pdf').status_code==200
    assert client.get(f'/ack/{app_id}.pdf').status_code==200
    assert rendered==['PENDING']
    with closing(ctx.database.connect()) as conn,conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE applications SET status='APPROVED' WHERE id=%s",(app_id,))
    assert client.get(f'/ack/{app_id}.pdf').status_code==200
    assert rendered==['PENDING','APPROVED']


def test_concurrent_receipt_requests_render_one_atomic_cache_entry(submission,monkeypatch):
    import time
    ctx=submission
    client=ctx.portal.app.test_client()
    data=prepare(client,ctx.mobile)
    assert client.post('/declaration',data=data,follow_redirects=True).status_code==200
    app_id=rows(ctx.database,ctx.mobile)[0][0]
    clients=[clone_client(ctx.portal,client) for _ in range(4)]
    original=ctx.portal.generate_acknowledgment
    calls=[]
    def slow(*args,**kwargs):
        calls.append(True);time.sleep(.1);return original(*args,**kwargs)
    monkeypatch.setattr(ctx.portal,'generate_acknowledgment',slow)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses=list(pool.map(lambda browser:browser.get(f'/ack/{app_id}.pdf'),clients))
    assert all(r.status_code==200 and r.data.startswith(b'%PDF-') for r in responses)
    assert len(calls)==1
    assert len({r.data for r in responses})==1


def test_pdf_failure_does_not_hide_accepted_status_and_receipt_can_retry(submission,monkeypatch):
    ctx=submission
    client=ctx.portal.app.test_client()
    data=prepare(client,ctx.mobile)
    assert client.post('/declaration',data=data,follow_redirects=True).status_code==200
    app_id,number,_=rows(ctx.database,ctx.mobile)[0]
    original=ctx.portal.generate_acknowledgment
    def unavailable(*args,**kwargs):
        raise RuntimeError('Synthetic renderer outage')
    monkeypatch.setattr(ctx.portal,'generate_acknowledgment',unavailable)
    assert client.get(f'/ack/{app_id}.pdf').status_code==503
    assert number.encode() in client.get(f'/application/{app_id}').data
    monkeypatch.setattr(ctx.portal,'generate_acknowledgment',original)
    assert client.get(f'/ack/{app_id}.pdf').status_code==200
    assert len(rows(ctx.database,ctx.mobile))==1
