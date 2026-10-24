"""Real bounded-pool and runtime-role verification on the owned PostgreSQL cluster."""
from contextlib import closing
import os
import secrets
from urllib.parse import quote
import pytest
import psycopg2
from conftest import ROOT, load_module


def test_missing_or_published_private_configuration_fails_closed(monkeypatch):
    import sys
    sys.path.insert(0,str(ROOT/'app'))
    from runtime import settings
    for key in ('SECRET_KEY','OTP_HMAC_KEY','DATABASE_URL','ADMIN_USERNAME','ADMIN_PASSWORD_HASH','PUBLIC_BASE_URL'):
        monkeypatch.delenv(key,raising=False)
    with pytest.raises(RuntimeError):
        settings()


def test_real_role_rotation_restricts_runtime_and_returns_pool_connections(database,monkeypatch):
    import sys
    sys.path.insert(0,str(ROOT/'app'))
    from runtime import Database
    manage = load_module('operator_manage',ROOT/'app/manage.py')
    password = secrets.token_hex(32)
    monkeypatch.setenv('RUNTIME_DATABASE_PASSWORD',password)
    monkeypatch.setenv('MIGRATION_DATABASE_PASSWORD',secrets.token_hex(32))
    with closing(database.connect()) as connection:
        manage.rotate(connection)
    config = {'mode':'test','database_url':'postgresql+psycopg2://portal_runtime:'+password+'@/postgres?host='+quote(database.connection['host'],safe=''),
        'pool_size':1,'pool_overflow':0}
    db = Database(config)
    try:
        conn = db.connect()
        with conn.cursor() as cur:
            cur.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user')
            assert cur.fetchone() == (False,False,False)
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute('CREATE TABLE forbidden_runtime_ddl(id INT)')
        conn.close()
        with closing(db.connect()) as recovered:
            with recovered.cursor() as cur:
                cur.execute('SELECT count(*) FROM applications')
                assert cur.fetchone()[0] >= 0
        assert db.engine.pool.checkedout() == 0
    finally:
        db.engine.dispose()


def test_concurrent_first_use_creates_one_shared_pool(database,monkeypatch):
    import sys
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    sys.path.insert(0,str(ROOT/'app'))
    import runtime
    original=runtime.create_engine
    calls=[]
    def slow_engine(*args,**kwargs):
        calls.append(True);time.sleep(.05);return original(*args,**kwargs)
    monkeypatch.setattr(runtime,'create_engine',slow_engine)
    db=runtime.Database({'mode':'test','database_url':'postgresql+psycopg2://sewasetu_test@/postgres?host='+quote(database.connection['host'],safe=''),
        'pool_size':2,'pool_overflow':0})
    barrier=threading.Barrier(4)
    def operation(index):
        barrier.wait()
        conn=db.connect()
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT 1');assert cur.fetchone()==(1,)
            conn.rollback()
        finally:conn.close()
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(operation,range(4)))
        assert len(calls)==1,'Concurrent startup allocated independent pools beyond the process connection budget'
        assert db.engine.pool.checkedout()==0
    finally:
        if db.engine:db.engine.dispose()
