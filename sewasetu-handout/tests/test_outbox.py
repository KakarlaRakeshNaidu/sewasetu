"""Actual job/outbox/gateway retries and crash boundary on private PostgreSQL."""
from datetime import timedelta
import sys
import pytest
import psycopg2
import requests
from conftest import ROOT,load_module
from test_deemed_approval import sla,sql,invoke,start,finish,INSTANT
sys.path.insert(0,str(ROOT/'app'))
from notifications import run_worker


def test_statutory_rerun_and_overlap_deduplicate_audit_and_outbox(sla):
    first=start(sla);second=start(sla)
    assert finish(first)['entry_exit']==0
    assert finish(second)['entry_exit']==0
    assert invoke(sla)['entry_exit']==0
    assert sql(sla,'SELECT count(*),count(DISTINCT event_key) FROM notification_outbox')==[(2,2)]
    assert sql(sla,"SELECT count(*) FROM application_audit WHERE event='DEEMED_APPROVED'")==[(2,)]


def test_queued_is_not_delivered_until_real_gateway_confirmation(sla):
    invoke(sla)
    sla.gateway.DELAY_MIN=sla.gateway.DELAY_MAX=1000
    assert run_worker(sla.database,INSTANT,gateway=sla.gateway_url)==2
    assert sql(sla,'SELECT DISTINCT status,delivered_at FROM notification_outbox')==[('QUEUED',None)]
    for message in sla.gateway.MESSAGES:
        message['delivery_at']=0
    assert run_worker(sla.database,INSTANT+timedelta(seconds=6),gateway=sla.gateway_url)==2
    assert sql(sla,"SELECT count(*) FROM notification_outbox WHERE status='DELIVERED' AND delivered_at IS NOT NULL")==[(2,)]
    assert run_worker(sla.database,INSTANT+timedelta(minutes=1),gateway=sla.gateway_url)==0
    assert len(sla.gateway.MESSAGES)==2


def test_transport_outage_backoff_and_recovery_preserve_decisions(sla,monkeypatch):
    invoke(sla)
    original=sla.gateway.app.view_functions['send']
    monkeypatch.setitem(sla.gateway.app.view_functions,'send',lambda: ({'status':'error'},503))
    run_worker(sla.database,INSTANT,gateway=sla.gateway_url)
    assert sql(sla,'SELECT DISTINCT status,attempts,last_error FROM notification_outbox')==[('PENDING',1,'HTTP_503')]
    assert run_worker(sla.database,INSTANT,gateway=sla.gateway_url)==0
    monkeypatch.setitem(sla.gateway.app.view_functions,'send',original)
    run_worker(sla.database,INSTANT+timedelta(seconds=11),gateway=sla.gateway_url)
    run_worker(sla.database,INSTANT+timedelta(seconds=17),gateway=sla.gateway_url)
    assert sql(sla,"SELECT count(*) FROM notification_outbox WHERE status='DELIVERED'")==[(2,)]
    assert sql(sla,"SELECT count(*) FROM applications WHERE status='DEEMED_APPROVED'")==[(3,)]


def test_gateway_send_then_database_failure_retries_same_event_without_duplicate(sla):
    invoke(sla)
    sql(sla,"""CREATE FUNCTION synthetic_outbox_fault() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN IF NEW.status='QUEUED' THEN RAISE EXCEPTION 'synthetic post-send database failure'; END IF; RETURN NEW; END $$""")
    sql(sla,'CREATE TRIGGER synthetic_outbox_fault BEFORE UPDATE ON notification_outbox FOR EACH ROW EXECUTE FUNCTION synthetic_outbox_fault()')
    try:
        with pytest.raises(psycopg2.Error):
            run_worker(sla.database,INSTANT,limit=1,gateway=sla.gateway_url)
        assert len(sla.gateway.MESSAGES)==1
    finally:
        sql(sla,'DROP TRIGGER synthetic_outbox_fault ON notification_outbox')
        sql(sla,'DROP FUNCTION synthetic_outbox_fault()')
    run_worker(sla.database,INSTANT+timedelta(seconds=61),gateway=sla.gateway_url)
    assert len(sla.gateway.MESSAGES)==2
    run_worker(sla.database,INSTANT+timedelta(seconds=67),gateway=sla.gateway_url)
    assert sql(sla,"SELECT count(*) FROM notification_outbox WHERE status='DELIVERED'")==[(2,)]


def test_permanent_send_failure_is_visible_and_bounded(sla,monkeypatch):
    invoke(sla)
    monkeypatch.setitem(sla.gateway.app.view_functions,'send',lambda: ({'status':'error'},503))
    for index in range(8):
        run_worker(sla.database,INSTANT+timedelta(hours=index),gateway=sla.gateway_url)
    assert sql(sla,'SELECT DISTINCT status,attempts,last_error FROM notification_outbox')==[('FAILED',8,'HTTP_503')]
    assert run_worker(sla.database,INSTANT+timedelta(days=1),gateway=sla.gateway_url)==0


def test_gateway_event_key_persists_across_process_module_restart(monkeypatch,tmp_path):
    monkeypatch.setenv('GATEWAY_STORE',str(tmp_path/'private-gateway/queue.json'))
    first=load_module('durable_gateway_one',ROOT/'smsgw/app.py').app.test_client()
    data={'to':'9100000099','text':'Synthetic durable notification','event_key':'synthetic-durable-event'}
    one=first.post('/api/send',json=data)
    assert one.status_code==200
    second_module=load_module('durable_gateway_two',ROOT/'smsgw/app.py')
    second=second_module.app.test_client()
    two=second.post('/api/send',json=data)
    assert two.json['id']==one.json['id'] and len(second_module.MESSAGES)==1
    assert second.post('/api/send',json=data|{'text':'Conflicting content'}).status_code==409
    assert (tmp_path/'private-gateway/queue.json').stat().st_mode & 0o077 == 0
