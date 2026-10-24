"""Revocable PostgreSQL sessions and purpose-bound OTP primitives."""
import hashlib
import hmac
import json
import secrets
from contextlib import contextmanager
from datetime import timedelta
from flask import abort, request, session
from flask.sessions import SessionInterface, SessionMixin
from werkzeug.datastructures import CallbackDict


class ServerSession(CallbackDict, SessionMixin):
    def __init__(self, values=None, sid=None, fresh=True, created=None):
        super().__init__(values or {})
        self.sid = sid or secrets.token_urlsafe(32)
        self.fresh, self.created = fresh, created


class Sessions(SessionInterface):
    def __init__(self, db, now, config):
        self.db, self.now, self.config = db, now, config

    @staticmethod
    def digest(sid):
        return hashlib.sha256(sid.encode()).hexdigest()

    def open_session(self, app, req):
        sid = req.cookies.get(app.config['SESSION_COOKIE_NAME'], '')
        now = self.now()
        if len(sid) == 43:
            with self.db() as cur:
                cur.execute("SELECT data,created_at,touched_at,expires_at,revoked FROM web_sessions WHERE token_hash=%s", (self.digest(sid),))
                row = cur.fetchone()
            if row and row[0].get('_epoch') == hashlib.sha256(self.config['secret'].encode()).hexdigest() and not row[4] and row[3] > now and (now-row[2]).total_seconds() < self.config['idle']:
                return ServerSession(row[0], sid, False, row[1])
            # Retain only the opaque owned draft reference, requiring fresh OTP before use.
            if row and not row[4] and row[0].get('draft_id'):
                return ServerSession({'resume_draft':row[0]['draft_id']})
        return ServerSession()

    def revoke(self, value):
        if not value.fresh:
            with self.db() as cur:
                cur.execute("UPDATE web_sessions SET revoked=TRUE,data='{}' WHERE token_hash=%s", (self.digest(value.sid),))

    def rotate(self, values):
        self.revoke(session)
        session.clear()
        session.update(values)
        session.sid, session.fresh, session.created = secrets.token_urlsafe(32), True, self.now()

    def save_session(self, app, value, response):
        now = self.now()
        created = value.created or now
        expiry = created + timedelta(seconds=self.config['absolute'])
        value['_epoch'] = hashlib.sha256(self.config['secret'].encode()).hexdigest()
        with self.db() as cur:
            if value.fresh:
                cur.execute("INSERT INTO web_sessions(token_hash,data,created_at,touched_at,expires_at) VALUES(%s,%s,%s,%s,%s)",
                    (self.digest(value.sid), json.dumps(dict(value)),created,now,expiry))
            else:
                # UPDATE avoids resurrecting a concurrently revoked session.
                cur.execute("UPDATE web_sessions SET data=%s,touched_at=%s WHERE token_hash=%s AND NOT revoked AND expires_at>%s",
                    (json.dumps(dict(value)), now,self.digest(value.sid),now))
                if cur.rowcount != 1:
                    response.delete_cookie(app.config['SESSION_COOKIE_NAME'])
                    return
        value.fresh, value.created = False, created
        response.set_cookie(app.config['SESSION_COOKIE_NAME'],value.sid,httponly=True,
            secure=self.config['secure'],samesite='Lax',path='/',max_age=self.config['absolute'])
        response.vary.add('Cookie')


def install(app, db, now, config):
    sessions = Sessions(db, now, config)
    app.session_interface = sessions
    def csrf_token():
        if 'csrf_token' not in session:
            session['csrf_token'] = secrets.token_urlsafe(32)
        return session['csrf_token']
    app.jinja_env.globals['csrf_token'] = csrf_token
    @app.before_request
    def csrf_guard():
        if request.method in ('POST','PUT','PATCH','DELETE'):
            actual = request.form.get('csrf_token','')
            expected = session.get('csrf_token','')
            if not expected or not hmac.compare_digest(actual,expected):
                abort(403)
            origin = request.headers.get('Origin')
            if origin and origin.rstrip('/') != config['public_origin']:
                abort(403)
    @app.after_request
    def private_headers(response):
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if config['secure']:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response
    return sessions


def issue(db, now, config, mobile, purpose, sender):
    instant = now()
    challenge, code = secrets.token_urlsafe(24), str(secrets.randbelow(900000)+100000)
    digest = hmac.new(config['otp_key'].encode(), (challenge+':'+purpose+':'+code).encode(),hashlib.sha256).hexdigest()
    with db() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('otp:'+mobile,))
        cur.execute("SELECT count(*),max(created_at) FROM portal_challenges WHERE mobile=%s AND created_at>%s", (mobile,instant-timedelta(hours=1)))
        count, latest = cur.fetchone()
        if count >= config['sends'] or (latest and (instant-latest).total_seconds()<config['cooldown']):
            return None
        cur.execute("UPDATE portal_challenges SET consumed=TRUE WHERE mobile=%s AND purpose=%s", (mobile,purpose))
        cur.execute("INSERT INTO portal_challenges(id,mobile,purpose,code_hash,created_at,expires_at) VALUES(%s,%s,%s,%s,%s,%s)",
            (challenge,mobile,purpose,digest,instant,instant+timedelta(seconds=config['otp_seconds'])))
    if not sender(mobile,'Your Sewa Setu OTP is '+code+'. Valid for 5 minutes.'):
        with db() as cur:
            cur.execute('UPDATE portal_challenges SET consumed=TRUE WHERE id=%s',(challenge,))
        return None
    return challenge


def consume(db, now, config, challenge, purpose, code):
    with db() as cur:
        cur.execute('SELECT mobile,code_hash,expires_at,attempts,consumed FROM portal_challenges WHERE id=%s AND purpose=%s FOR UPDATE',(challenge,purpose))
        row = cur.fetchone()
        if not row or row[4] or row[2]<=now() or row[3]>=config['attempts']:
            return None
        digest = hmac.new(config['otp_key'].encode(),(challenge+':'+purpose+':'+code).encode(),hashlib.sha256).hexdigest()
        ok = hmac.compare_digest(digest,row[1])
        cur.execute('UPDATE portal_challenges SET attempts=attempts+1,consumed=%s WHERE id=%s',(ok or row[3]+1>=config['attempts'],challenge))
        return row[0] if ok else None
