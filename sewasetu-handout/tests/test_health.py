"""Health probes disclose release only, bypass sessions, and fail closed on dependencies."""
from pathlib import Path
import psycopg2


def test_health_readiness_uses_real_database_and_gateway_without_session_cookie(portal,gateway):
    Path(portal.UPLOAD_DIR).mkdir()
    client=portal.app.test_client()
    for path,status in [('/health/live','live'),('/health/ready','ready')]:
        response=client.get(path)
        assert response.status_code==200 and response.json['status']==status
        assert set(response.json)=={'status','release'}
        assert 'Set-Cookie' not in response.headers
    with portal.transaction() as cur:
        cur.execute('SELECT count(*) FROM web_sessions')
        assert cur.fetchone()[0]==0


def test_readiness_fails_when_database_unavailable_but_liveness_survives(portal,monkeypatch):
    def unavailable():raise psycopg2.OperationalError('Synthetic database outage')
    monkeypatch.setattr(portal,'get_db',unavailable)
    client=portal.app.test_client()
    assert client.get('/health/live').status_code==200
    response=client.get('/health/ready')
    assert response.status_code==503 and response.json['status']=='unavailable'
    assert b'Synthetic' not in response.data and 'Set-Cookie' not in response.headers


def test_readiness_fails_when_gateway_is_not_available(portal,monkeypatch):
    Path(portal.UPLOAD_DIR).mkdir()
    monkeypatch.setattr(portal,'SMS_GATEWAY_URL','http://127.0.0.1:1')
    response=portal.app.test_client().get('/health/ready')
    assert response.status_code==503 and b'127.0.0.1' not in response.data
