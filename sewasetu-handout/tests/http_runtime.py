"""Owned loopback Gunicorn/PostgreSQL/gateway runtime for browser verification."""
from contextlib import ExitStack
from pathlib import Path
import importlib.util
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import quote
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server,WSGIRequestHandler
from pg_runtime import PostgresSandbox
from submission_support import HttpClient
ROOT=Path(__file__).resolve().parents[1]


class Quiet(WSGIRequestHandler):
    def log_request(self,*args,**kwargs):pass


class HttpRuntime:
    def __enter__(self):
        self.stack=ExitStack();self.master=None;self.server=None
        try:
            self.db=self.stack.enter_context(PostgresSandbox())
            self.work=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='sewasetu-browser-')))
            self.db.load(ROOT/'app/schema.sql')
            for migration in sorted((ROOT/'app/migrations').glob('*.sql')):self.db.load(migration)
            spec=importlib.util.spec_from_file_location('browser_sms_gateway',ROOT/'smsgw/app.py')
            gateway=importlib.util.module_from_spec(spec);sys.modules[spec.name]=gateway;spec.loader.exec_module(gateway)
            gateway.DELAY_MIN=gateway.DELAY_MAX=0
            self.server=make_server('127.0.0.1',0,gateway.app,request_handler=Quiet)
            self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
            listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128)
            self.url='http://127.0.0.1:'+str(listener.getsockname()[1])
            self.staff_password='Synthetic-Only-Staff-Password'
            secret=secrets.token_hex(32)
            env=dict(os.environ,APP_ENV='test',SECRET_KEY=secret,OTP_HMAC_KEY=secrets.token_hex(32),
                ADMIN_USERNAME='synthetic-staff',ADMIN_PASSWORD_HASH=generate_password_hash(self.staff_password),
                PUBLIC_BASE_URL=self.url,SESSION_COOKIE_SECURE='false',OTP_RESEND_COOLDOWN_SECONDS='1',
                DATABASE_URL='postgresql+psycopg2://sewasetu_test@/postgres?host='+quote(str(self.db.socket),safe=''),
                SMS_GATEWAY_URL=f'http://127.0.0.1:{self.server.server_port}',UPLOAD_DIR=str(self.work/'uploads'),
                REPLAY_PG_SOCKET=str(self.db.socket),REPLAY_SMS_URL=f'http://127.0.0.1:{self.server.server_port}',
                REPLAY_UPLOAD_DIR=str(self.work/'uploads'),REPLAY_SECRET=secret,REPLAY_CLOCK_EPOCH=str(time.time()),PYTHONDONTWRITEBYTECODE='1')
            env.pop('GUNICORN_CMD_ARGS',None)
            self.log=self.stack.enter_context((self.work/'server.log').open('w+'))
            self.master=subprocess.Popen([str(Path(sys.executable).parent/'gunicorn'),'--chdir',str(ROOT/'tests'),'--bind','fd://'+str(listener.fileno()),
                '--workers','2','--threads','2','--timeout','30','replay_portal:app'],env=env,pass_fds=(listener.fileno(),),
                stdout=self.log,stderr=self.log,start_new_session=True)
            listener.close()
            client=HttpClient(self.url)
            try:
                for _ in range(80):
                    assert self.master.poll() is None,'Owned browser server exited'
                    try:
                        if client.get('/').status_code==200:return self
                    except Exception:pass
                    time.sleep(.1)
                raise RuntimeError('Owned browser server did not become ready')
            finally:client.close()
        except BaseException:
            self.__exit__(None,None,None);raise

    def __exit__(self,*args):
        if self.master is not None and self.master.poll() is None:
            os.killpg(self.master.pid,signal.SIGTERM)
            try:self.master.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.master.pid,signal.SIGKILL);self.master.wait(timeout=5)
        if self.server is not None:
            self.server.shutdown();self.thread.join(timeout=5);self.server.server_close()
        self.stack.close()
