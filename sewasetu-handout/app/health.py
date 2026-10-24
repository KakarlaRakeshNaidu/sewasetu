"""Bounded, identifier-free liveness and dependency readiness."""
import os
from pathlib import Path
import requests
from flask import jsonify


def install(portal):
    release_path=Path(__file__).parent/'RELEASE'
    release=release_path.read_text().strip() if release_path.exists() else 'development'
    @portal.app.get('/health/live')
    def live():return jsonify(status='live',release=release)
    @portal.app.get('/health/ready')
    def ready():
        try:
            with portal.transaction() as cur:
                cur.execute('SELECT a.receipt_version,d.version,d.kind,c.purpose,o.status FROM applications a CROSS JOIN application_drafts d CROSS JOIN portal_challenges c CROSS JOIN notification_outbox o LIMIT 0')
            if not os.access(portal.UPLOAD_DIR,os.W_OK):raise OSError('uploads unavailable')
            response=requests.get(portal.SMS_GATEWAY_URL+'/health',timeout=(1,2),allow_redirects=False)
            if response.status_code!=200:raise OSError('gateway unavailable')
            return jsonify(status='ready',release=release)
        except Exception:
            return jsonify(status='unavailable',release=release),503
