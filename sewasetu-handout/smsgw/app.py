"""PurvSMS - simulated state SMS gateway.

Stands in for the telecom SMS provider. Messages sent via the API appear on the
web console at / once "delivered", after a short carrier latency.
"""

import random
import os
import json
import tempfile
import threading
import fcntl
import logging
from contextlib import contextmanager
from pathlib import Path
import time
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

MESSAGES = []  # {id, to, text, sent_at, delivery_at}

# Carrier queue latency, seconds.
DELAY_MIN = 1
DELAY_MAX = 4

CONSOLE = """
<!doctype html><html><head><title>PurvSMS Console</title>
<meta http-equiv="refresh" content="5">
<style>
body{font-family:Arial,sans-serif;margin:24px;background:#f4f4f4}
h2{color:#1a3c6e} table{border-collapse:collapse;background:#fff;width:100%}
td,th{border:1px solid #ccc;padding:6px 10px;font-size:14px;text-align:left}
.q{color:#b36b00}.d{color:#1b7e3c}
</style></head><body>
<h2>PurvSMS Gateway Console (simulation)</h2>
<p>Messages appear below once the carrier delivers them. Auto-refreshes every 5s.</p>
<table><tr><th>#</th><th>To</th><th>Message</th><th>Sent</th><th>Status</th></tr>
{% for m in messages %}
<tr><td>{{m.id}}</td><td>{{m.to}}</td>
<td>{% if m.delivered %}{{m.text}}{% else %}<i>(in transit)</i>{% endif %}</td>
<td>{{m.sent_hhmm}}</td>
<td>{% if m.delivered %}<span class="d">Delivered</span>
{% else %}<span class="q">Queued &middot; ~{{m.eta}}s</span>{% endif %}</td></tr>
{% endfor %}
</table></body></html>
"""


_lock = threading.RLock()
_next_id = 0


@contextmanager
def queue_store(write=False):
    """Private single-file durable simulation queue; flock covers multiple gateway workers."""
    global _next_id
    filename = os.environ.get('GATEWAY_STORE')
    with _lock:
        if not filename:
            yield
            return
        path = Path(filename)
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        with open(str(path)+'.lock','a+') as lock:
            os.chmod(lock.name,0o600)
            fcntl.flock(lock,fcntl.LOCK_EX)
            if path.exists():
                data = json.loads(path.read_text())
                MESSAGES[:] = data['messages']
                _next_id = data['next_id']
            yield
            if write:
                fd,temporary = tempfile.mkstemp(dir=path.parent,prefix='queue-')
                try:
                    with os.fdopen(fd,'w') as output:
                        json.dump({'next_id':_next_id,'messages':MESSAGES},output)
                        output.flush();os.fsync(output.fileno())
                    os.replace(temporary,path)
                    directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
                    try:os.fsync(directory)
                    finally:os.close(directory)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)


@app.get('/health')
def health():
    try:
        with queue_store():pass
        path=os.environ.get('GATEWAY_STORE')
        if path and not os.access(Path(path).parent,os.W_OK):raise OSError()
        return jsonify(status='ready')
    except (OSError,ValueError,KeyError):return jsonify(status='unavailable'),503


@app.route('/api/send',methods=['POST'])
def send():
    global _next_id
    data = request.get_json(force=True,silent=True) or request.form
    to,text,event = data.get('to',''),data.get('text',''),data.get('event_key')
    if not isinstance(to,str) or not isinstance(text,str) or not to or len(to)>15 or not text or len(text)>1000 or (event is not None and (not isinstance(event,str) or len(event)>120)):
        return jsonify(status='error',reason='invalid message'),400
    try:
        with queue_store(write=True):
            now = time.time()
            # Retention exceeds bounded retry/lease windows; accepted SMS cannot be unsent.
            MESSAGES[:] = [m for m in MESSAGES if now-m['sent_at']<7*86400]
            if event:
                previous = next((m for m in MESSAGES if m.get('event_key')==event),None)
                if previous:
                    if previous['to']!=to or previous['text']!=text:
                        return jsonify(status='error',reason='event conflict'),409
                    return jsonify(status='queued',id=previous['id'])
            if len(MESSAGES)>=50000:
                return jsonify(status='error',reason='queue full'),503
            _next_id+=1
            msg={'id':_next_id,'to':to,'text':text,'event_key':event,'sent_at':now,
                 'delivery_at':now+random.randint(DELAY_MIN,DELAY_MAX)}
            MESSAGES.append(msg)
            return jsonify(status='queued',id=msg['id'])
    except (OSError,ValueError,KeyError):
        return jsonify(status='error',reason='queue storage unavailable'),503


@app.route("/api/messages")
def messages():
    # Inbox query for a given number. Used to read delivered OTPs/SMS.
    to = request.args.get("to", "")
    with queue_store():
        snapshot = list(MESSAGES)
    now = time.time()
    out = []
    for m in snapshot:
        if to and m["to"] != to:
            continue
        out.append({
            "id": m["id"], "to": m["to"],
            "text": m["text"] if now >= m["delivery_at"] else None,
            "delivered": now >= m["delivery_at"],
            "sent_at": m["sent_at"], "delivery_at": m["delivery_at"],
        })
    return jsonify({"messages": out})


@app.route("/")
def console():
    with queue_store():
        snapshot = list(MESSAGES)
    now = time.time()
    view = []
    for m in reversed(snapshot[-100:]):
        view.append({
            "id": m["id"], "to": m["to"], "text": m["text"],
            "sent_hhmm": time.strftime("%H:%M:%S", time.localtime(m["sent_at"])),
            "delivered": now >= m["delivery_at"],
            "eta": max(0, int(m["delivery_at"] - now)),
        })
    return render_template_string(CONSOLE, messages=view)


logging.getLogger("werkzeug").setLevel(logging.ERROR)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8025)
