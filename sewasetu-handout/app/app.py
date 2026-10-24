# -*- coding: utf-8 -*-
"""
Sewa Setu - Old Age Pension Portal
Government of Purvanchal, Department of Social Welfare

Developed by: Netlink Infosolutions Pvt Ltd (2023)
Maintenance contract ended 31/01/2026.
"""

import os
import io
import csv
import random
import re
import hashlib
import configparser
from datetime import datetime, date, timedelta

import requests
import psycopg2
from flask import (Flask, request, session, redirect, url_for, render_template,
                   flash, send_file, abort, jsonify)
from fpdf import FPDF

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Shared settings are mandatory; inherited handout defaults are never loaded.
import sys
import secrets
import hmac
import json
from contextlib import contextmanager
from zoneinfo import ZoneInfo
from flask import g
from werkzeug.security import check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
sys.path.insert(0, BASE_DIR)
from runtime import settings, Database
from access import install, issue, consume

SETTINGS = settings()
app = Flask(__name__)
app.secret_key = SETTINGS['secret']
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=SETTINGS['secure'], MAX_CONTENT_LENGTH=6*1024*1024)
# Only enable behind the single private trusted proxy configured by deployment.
if os.environ.get('TRUST_PROXY') == '1':
    app.wsgi_app = ProxyFix(app.wsgi_app,x_for=1,x_proto=1,x_host=1)
ADMIN_USERNAME = SETTINGS['admin']
SMS_GATEWAY_URL = os.environ.get('SMS_GATEWAY_URL','http://smsgw:8025').rstrip('/')
OTP_VALIDITY_SECONDS = SETTINGS['otp_seconds']
UPLOAD_DIR = os.environ.get('UPLOAD_DIR',os.path.join(BASE_DIR,'..','uploads'))
SCHEME_DEADLINE = SETTINGS['deadline']
MIN_AGE, SLA_DAYS = int(os.environ.get('MIN_AGE','60')), int(os.environ.get('SLA_DAYS','15'))
BLOCKS = ['Sonari','Rajapara','Dhemaji Pathar','Borgaon','Namti','Khelua']
_database = Database(SETTINGS)


def now():
    return datetime.now(ZoneInfo('Asia/Kolkata'))


def get_db():
    return _database.connect()


def open_db():
    connection = get_db()
    g.setdefault('database_connections',[]).append(connection)
    return connection


@app.teardown_request
def release_connections(error):
    for connection in g.pop('database_connections',[]):
        try:
            if not getattr(connection,'closed',True):
                connection.rollback()
        finally:
            connection.close()


@contextmanager
def transaction():
    connection = get_db()
    try:
        with connection.cursor() as cursor:
            yield cursor
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


sessions = install(app,transaction,now,SETTINGS)


from sqlalchemy.exc import TimeoutError as PoolTimeout, OperationalError as EngineUnavailable

@app.errorhandler(psycopg2.Error)
@app.errorhandler(PoolTimeout)
@app.errorhandler(EngineUnavailable)
def database_unavailable(error):
    app.logger.warning('Database operation unavailable; transaction rolled back')
    return 'Service temporarily unavailable. Please retry shortly or check existing application status.',503


@app.before_request
def staff_boundary():
    if request.path.startswith('/admin/') and (session.get('role') != 'staff' or session.get('actor') != ADMIN_USERNAME):
        if request.method == 'POST':
            abort(403)
        return redirect(url_for('admin_login'))


@app.post('/logout')
def logout():
    sessions.rotate({})
    return redirect(url_for('index'))


def sanitize(value, maxlen=20):
    # Trim input to safe length for downstream systems (NIC mandate 2019:
    # legacy disbursement interface accepts fixed-width fields).
    if value is None:
        return ""
    value = value.strip()
    encoded = value.encode("utf-8")[:maxlen]
    return encoded.decode("utf-8", errors="ignore")


def hash_password(p):
    return hashlib.sha256(p.encode("utf-8")).hexdigest()


def send_sms(mobile, text):
    try:
        response = requests.post(SMS_GATEWAY_URL+'/api/send',json={'to':mobile,'text':text},timeout=5)
        return response.status_code in (200,202)
    except requests.RequestException:
        app.logger.warning('SMS transport unavailable')
        return False


def deadline_remaining():
    seconds = (SCHEME_DEADLINE-now()).total_seconds()
    return None if seconds <= 0 else int(seconds//3600)


def new_application_no():
    return "SSP" + datetime.now().strftime("%y") + str(random.randint(100000, 999999))


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", hours_left=deadline_remaining())


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/__gateway/", defaults={"subpath": ""})
@app.route("/__gateway/<path:subpath>")
def gateway_proxy(subpath):
    if subpath not in ('','api/messages'):
        abort(404)
    try:
        r = requests.get(SMS_GATEWAY_URL + "/" + subpath,
                         params=request.args, timeout=5)
        return (r.content, r.status_code,
                {"Content-Type": r.headers.get("Content-Type", "text/html")})
    except Exception as e:
        return ("SMS gateway unreachable: %s" % e, 502)


# ---------------------------------------------------------------------------
# Application flow: mobile -> OTP -> form steps -> upload -> declaration
# ---------------------------------------------------------------------------

@app.route('/apply',methods=['GET','POST'])
def apply():
    if deadline_remaining() is None:
        flash('The application window for this scheme has closed.')
        return redirect(url_for('index'))
    if request.method == 'POST':
        mobile = request.form.get('mobile','').strip()
        if not re.fullmatch(r'[6-9][0-9]{9}',mobile) or not isinstance(session.get('captcha_answer'),int) or request.form.get('captcha') != str(session.get('captcha_answer')):
            flash('Enter a valid ten-digit mobile number and answer the addition question.')
        else:
            challenge = issue(transaction,now,SETTINGS,mobile,'apply',send_sms)
            if challenge:
                reference = session.get('draft_id') or session.get('resume_draft')
                sessions.rotate({'challenge':challenge,'purpose':'apply','apply_mobile':mobile,'resume_draft':reference})
                return redirect(url_for('verify'))
            flash('A code was recently requested or delivery is unavailable. Please wait before retrying; your draft is saved.')
    return render_template('apply.html',captcha_q=make_captcha())


def make_captcha():
    a, b = random.randint(1, 9), random.randint(1, 9)
    session["captcha_answer"] = a + b
    return "%d + %d" % (a, b)


def verified_challenge(purpose):
    mobile = consume(transaction,now,SETTINGS,session.get('challenge',''),purpose,request.form.get('otp','').strip())
    if not mobile:
        flash('Code invalid, expired or already used. Request a new code; your draft is saved.')
        return None
    reference = session.get('resume_draft')
    values = {'role':'citizen','logged_in':True,'portal_mobile':mobile}
    if purpose == 'apply':
        values['verified_mobile'] = mobile
        with transaction() as cur:
            cur.execute('SELECT id,data FROM application_drafts WHERE id=%s AND mobile=%s AND expires_at>%s',(reference,mobile,now()))
            draft = cur.fetchone()
            if draft:
                values.update(draft_id=draft[0],form_data=draft[1].get('form_data',{}),doc_path=draft[1].get('doc_path',''))
            else:
                reference = secrets.token_urlsafe(24)
                cur.execute("INSERT INTO application_drafts(id,mobile,touched_at,expires_at) VALUES(%s,%s,%s,%s)",
                    (reference,mobile,now(),now()+timedelta(days=7)))
                values['draft_id'] = reference
    if purpose == 'recovery':
        with transaction() as cur:
            cur.execute("UPDATE web_sessions SET revoked=TRUE,data='{}' WHERE data->>'portal_mobile'=%s AND data->>'role'='citizen'",(mobile,))
    sessions.rotate(values)
    return mobile


@app.route('/verify',methods=['GET','POST'])
def verify():
    if session.get('purpose') != 'apply':
        return redirect(url_for('apply'))
    if request.method == 'POST' and verified_challenge('apply'):
        return redirect(url_for('form_step',step=1))
    return render_template('verify.html',mobile=session.get('apply_mobile',''))


def save_draft():
    if session.get('draft_id') and session.get('verified_mobile'):
        with transaction() as cur:
            cur.execute('UPDATE application_drafts SET data=%s,touched_at=%s WHERE id=%s AND mobile=%s',
                (json.dumps({'form_data':session.get('form_data',{}),'doc_path':session.get('doc_path','')}),now(),session['draft_id'],session['verified_mobile']))


@app.route("/form/<int:step>", methods=["GET", "POST"])
def form_step(step):
    if not session.get("verified_mobile"):
        flash("Session expired. Please verify your mobile number again.")
        return redirect(url_for("apply"))
    if step not in (1, 2, 3):
        abort(404)
    if request.method == "POST":
        data = session.get("form_data", {})
        allowed = {1:('applicant_name','dob','gender','marital_status','husband_name','husband_employer'),
                   2:('village','block'),3:('bank_account','ifsc')}
        for k in allowed[step]:
            if k in request.form:
                data[k] = request.form[k]
        session["form_data"] = data
        save_draft()
        if step < 3:
            return redirect(url_for("form_step", step=step + 1))
        return redirect(url_for("upload"))
    return render_template("form_step%d.html" % step,
                           data=session.get("form_data", {}), blocks=BLOCKS)


@app.route("/upload", methods=["GET", "POST"])
def upload():
    if not session.get("verified_mobile"):
        flash("Session expired. Please verify your mobile number again.")
        return redirect(url_for("apply"))
    if request.method == "POST":
        f = request.files.get("document")
        if f is None or f.filename == "":
            flash("ERR_VAL_47")
            return render_template("upload.html")
        filename = f.filename.lower()
        content = f.read()
        if not filename.endswith(".pdf"):
            flash("ERR_VAL_47")
            return render_template("upload.html")
        if len(content) > 102400:
            flash("ERR_VAL_47")
            return render_template("upload.html")
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        path = os.path.join(UPLOAD_DIR, secrets.token_hex(24) + ".pdf")
        with open(path, "wb") as out:
            out.write(content)
        session["doc_path"] = path
        save_draft()
        return redirect(url_for("declaration"))
    return render_template("upload.html")


@app.route("/declaration", methods=["GET", "POST"])
def declaration():
    if not session.get("verified_mobile"):
        flash("Session expired. Please verify your mobile number again.")
        return redirect(url_for("apply"))
    if request.method == "POST":
        return handle_submission()
    return render_template("declaration.html")


def handle_submission():
    # TODO: split this up some day. It grew. - RK, 09/2024
    mobile = session.get("verified_mobile")
    data = session.get("form_data", {})
    doc_path = session.get("doc_path", "")

    name = sanitize(data.get("applicant_name", ""))
    village = data.get("village", "").strip()
    block = data.get("block", "").strip()
    gender = data.get("gender", "")
    marital = data.get("marital_status", "")
    husband_name = data.get("husband_name", "")
    husband_employer = data.get("husband_employer", "")
    bank_account = data.get("bank_account", "").strip()
    ifsc = data.get("ifsc", "").strip().upper()
    dob_raw = data.get("dob", "").strip()

    if not name or not village or not block or not bank_account or not dob_raw:
        flash("Something went wrong. Please try again.")
        return redirect(url_for("form_step", step=1))

    if marital == "Widowed":
        if not husband_name or not husband_employer:
            flash("Something went wrong. Please try again.")
            return redirect(url_for("form_step", step=1))

    # lenient date parsing to reduce rejections (CR-2024-117)
    dob = None
    for fmt in ("%m/%d/%Y", "%d/%m/%Y"):
        try:
            dob = datetime.strptime(dob_raw, fmt).date()
            break
        except ValueError:
            continue
    if dob is None:
        flash("Something went wrong. Please try again.")
        return redirect(url_for("form_step", step=1))

    today = date.today()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    if age < MIN_AGE:
        flash("Applicant must be above %d years of age to be eligible." % MIN_AGE)
        return redirect(url_for("form_step", step=1))

    if now() >= SCHEME_DEADLINE:
        flash("The application deadline has passed.")
        return redirect(url_for("index"))

    conn = open_db()
    cur = conn.cursor()

    cur.execute("SELECT count(*) FROM applications WHERE mobile = %s", (mobile,))
    if cur.fetchone()[0] > 0:
        cur.close(); conn.close()
        flash("An application already exists for this mobile number. "
              "Duplicate applications are not permitted.")
        return redirect(url_for("index"))

    app_no = new_application_no()
    cur.execute(
        """INSERT INTO applications
           (application_no, applicant_name, mobile, dob, gender, marital_status,
            husband_name, husband_employer, village, block, bank_account, ifsc,
            doc_path, status, submitted_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING',%s)
           RETURNING id""",
        (app_no, name, mobile, dob, gender, marital, husband_name,
         husband_employer, village, block, bank_account, ifsc, doc_path,
         datetime.now()))
    new_id = cur.fetchone()[0]

    # status portal account; password is DOB as DDMMYYYY per dept. circular
    portal_pass = dob.strftime("%d%m%Y")
    cur.execute("SELECT count(*) FROM portal_users WHERE mobile = %s", (mobile,))
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO portal_users (mobile, password_hash) VALUES (%s,%s)",
                    (mobile, hash_password(portal_pass)))
    conn.commit()

    app.logger.info("generating acknowledgment pdf application=%d" % new_id)
    pdf_bytes = generate_acknowledgment(cur, new_id)
    ack_dir = os.path.join(UPLOAD_DIR, "ack")
    os.makedirs(ack_dir, exist_ok=True)
    with open(os.path.join(ack_dir, "%d.pdf" % new_id), "wb") as out:
        out.write(pdf_bytes)

    cur.close(); conn.close()
    session.pop("form_data", None)
    session.pop("doc_path", None)
    session.pop("verified_mobile", None)
    send_sms(mobile, "Sewa Setu: application %s received. Track at the status portal "
                     "with mobile no. and password (DOB as DDMMYYYY)." % app_no)
    return render_template("confirmation.html", app_no=app_no, app_id=new_id)


def generate_acknowledgment(cur, app_id):
    cur.execute("SELECT application_no, applicant_name, mobile, dob, village, block, "
                "bank_account, ifsc, submitted_at, status FROM applications WHERE id = %s",
                (app_id,))
    row = cur.fetchone()
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "GOVERNMENT OF PURVANCHAL", ln=1, align="C")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, "Department of Social Welfare", ln=1, align="C")
    pdf.cell(0, 8, "Old Age Pension Scheme - Acknowledgment", ln=1, align="C")
    pdf.ln(4)
    # decorative border, as per approved letterhead design
    for i in range(0, 2000):
        x = 10 + (i % 190)
        pdf.line(x, 282, x + 0.5, 282)
        pdf.line(x, 12, x + 0.5, 12)
    labels = ["Application No", "Applicant Name", "Mobile", "Date of Birth",
              "Village", "Block", "Bank Account", "IFSC", "Submitted At", "Status"]
    pdf.set_font("Helvetica", "", 10)
    for label, val in zip(labels, row):
        try:
            pdf.cell(60, 8, label, border=1)
            pdf.cell(0, 8, str(val), border=1, ln=1)
        except Exception:
            pdf.cell(0, 8, "?", border=1, ln=1)
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 9)
    pdf.multi_cell(0, 5, "This is a computer generated acknowledgment. Processing SLA "
                         "as per the Purvanchal Right to Public Services Act applies.")
    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# Status portal (citizen login)
# ---------------------------------------------------------------------------

@app.route('/status',methods=['GET','POST'])
def status_login():
    if request.method == 'POST':
        mobile = request.form.get('mobile','').strip()
        if re.fullmatch(r'[6-9][0-9]{9}',mobile):
            challenge = issue(transaction,now,SETTINGS,mobile,'status',send_sms)
            if challenge:
                sessions.rotate({'challenge':challenge,'purpose':'status','apply_mobile':mobile})
                return redirect(url_for('status_verify'))
        flash('If you can receive a code, request verification after the waiting period.')
    return render_template('status_login.html')


@app.route('/status/verify',methods=['GET','POST'])
def status_verify():
    if session.get('purpose') not in ('status','recovery'):
        return redirect(url_for('status_login'))
    if request.method == 'POST' and verified_challenge(session['purpose']):
        with transaction() as cur:
            cur.execute('SELECT id FROM applications WHERE mobile=%s ORDER BY submitted_at DESC,id DESC LIMIT 1',(session['portal_mobile'],))
            row = cur.fetchone()
        if row:
            return redirect(url_for('view_application',app_id=row[0]))
        flash('No application is associated with this verified contact.')
        return redirect(url_for('status_login'))
    return render_template('verify.html',mobile=session.get('apply_mobile',''))


def authorize_record(app_id):
    if session.get('role') not in ('citizen','staff'):
        return False
    with transaction() as cur:
        cur.execute('SELECT mobile FROM applications WHERE id=%s',(app_id,))
        row = cur.fetchone()
    return bool(row and ((session.get('role') == 'staff' and session.get('actor') == ADMIN_USERNAME) or (session.get('role') == 'citizen' and row[0] == session.get('portal_mobile'))))


@app.route("/application/<int:app_id>")
def view_application(app_id):
    if not authorize_record(app_id):
        flash("Please login to view application status.")
        return redirect(url_for("status_login"))
    conn = open_db()
    cur = conn.cursor()
    cur.execute("SELECT application_no, applicant_name, mobile, dob, village, block, "
                "bank_account, ifsc, status, submitted_at, decided_at "
                "FROM applications WHERE id = %s", (app_id,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        abort(404)
    return render_template("application.html", a=row, app_id=app_id)


@app.route("/ack/<int:app_id>.pdf")
def ack_pdf(app_id):
    if not authorize_record(app_id):
        abort(403)
    path = os.path.join(UPLOAD_DIR, "ack", "%d.pdf" % app_id)
    if not os.path.exists(path):
        conn = open_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM applications WHERE id = %s", (app_id,))
        if not cur.fetchone():
            cur.close(); conn.close()
            abort(404)
        data = generate_acknowledgment(cur, app_id)
        cur.close(); conn.close()
        return send_file(io.BytesIO(data), mimetype="application/pdf")
    return send_file(path, mimetype="application/pdf")


@app.route('/status/reset',methods=['GET','POST'])
def reset_password():
    if request.method == 'POST':
        mobile = request.form.get('mobile','').strip()
        if re.fullmatch(r'[6-9][0-9]{9}',mobile):
            challenge = issue(transaction,now,SETTINGS,mobile,'recovery',send_sms)
            if challenge:
                sessions.rotate({'challenge':challenge,'purpose':'recovery','apply_mobile':mobile})
                return redirect(url_for('status_verify'))
        flash('If you can receive a code, use verification to recover access. Existing passwords are unchanged.')
    return render_template('reset.html')


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if (request.form.get("username") == ADMIN_USERNAME and
                check_password_hash(SETTINGS["admin_hash"], request.form.get("password", ""))):
            sessions.rotate({"logged_in":True,"admin":True,"role":"staff","actor":ADMIN_USERNAME})
            return redirect(url_for("admin_dashboard"))
        flash("Invalid credentials.")
    return render_template("admin_login.html")


@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    conn = open_db()
    cur = conn.cursor()
    cur.execute("SELECT status, count(*) FROM applications GROUP BY status")
    by_status = cur.fetchall()
    cur.execute("SELECT count(*) FROM applications WHERE status = 'PENDING' "
                "AND submitted_at < %s", (datetime.now() - timedelta(days=SLA_DAYS),))
    overdue = cur.fetchone()[0]
    cur.execute("SELECT block, count(*) FROM applications WHERE status = 'PENDING' "
                "GROUP BY block ORDER BY count(*) DESC")
    by_block = cur.fetchall()
    cur.close(); conn.close()
    return render_template("admin_dashboard.html", by_status=by_status,
                           overdue=overdue, by_block=by_block, sla=SLA_DAYS)


@app.route("/admin/applications")
def admin_list():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    status = request.args.get("status", "PENDING")
    try:
        page = min(10000,max(1,int(request.args.get("page",1))))
    except ValueError:
        abort(400)
    conn = open_db()
    cur = conn.cursor()
    cur.execute("SELECT id, application_no, applicant_name, mobile, block, status, "
                "submitted_at FROM applications WHERE status = %s "
                "ORDER BY submitted_at ASC,id ASC LIMIT 50 OFFSET %s",
                (status, (page - 1) * 50))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return render_template("admin_list.html", rows=rows, status=status, page=page)


@app.route("/admin/application/<int:app_id>")
def admin_view(app_id):
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    conn = open_db()
    cur = conn.cursor()
    cur.execute("SELECT application_no, applicant_name, mobile, dob, gender, "
                "marital_status, village, block, bank_account, ifsc, doc_path, "
                "status, submitted_at, decided_at, decided_by "
                "FROM applications WHERE id = %s", (app_id,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        abort(404)
    return render_template("admin_view.html", a=row, app_id=app_id)


def decide(app_id,status):
    if session.get('role') != 'staff':
        abort(403)
    instant = now()
    with transaction() as cur:
        cur.execute("UPDATE applications SET status=%s,decided_at=%s,decided_by=%s WHERE id=%s AND status='PENDING' RETURNING id",
            (status,instant.replace(tzinfo=None),session['actor'],app_id))
        if cur.fetchone():
            cur.execute('INSERT INTO application_audit(application_id,event,actor,occurred_at) VALUES(%s,%s,%s,%s)',
                (app_id,status,session['actor'],instant))
        else:
            flash('This application is already decided or unavailable.')
    return redirect(url_for('admin_list'))


@app.post('/admin/approve/<int:app_id>')
def admin_approve(app_id):
    return decide(app_id,'APPROVED')


@app.post('/admin/reject/<int:app_id>')
def admin_reject(app_id):
    return decide(app_id,'REJECTED')


# ---------------------------------------------------------------------------
# Unused / legacy
# ---------------------------------------------------------------------------

def export_to_excel(rows):
    # Was used for the monthly disbursement report before eKosh integration.
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["application_no", "name", "account", "ifsc", "amount"])
    for r in rows:
        writer.writerow(list(r) + ["250"])
    return out.getvalue()


def old_payment_gateway_callback(txn):
    # retained for reference; eChallan integration was descoped in Phase 2
    status = txn.get("STATUS")
    if status == "0300":
        return "SUCCESS"
    elif status == "0399":
        return "FAILED"
    return "PENDING"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
