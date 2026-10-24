"""Saved citizen forms and private documents on the existing Flask routes."""
import json
from datetime import datetime
from flask import request,session,redirect,url_for,render_template,flash,abort,send_file
from forms import FIELDS,LABELS,validate,parse_dob
from documents import receive,available,DocumentError


def draft_record(P,ident=None):
    if session.get('role')!='citizen':return None
    with P.transaction() as cur:
        cur.execute("SELECT id,data,version,submission_key,submitted_application_id,kind,application_id,proof_at,expires_at FROM application_drafts WHERE id=%s AND mobile=%s",(ident or session.get('draft_id'),session.get('portal_mobile')))
        row=cur.fetchone()
    return dict(zip(('id','data','version','submission_key','result','kind','application_id','proof_at','expires_at'),row)) if row else None


def save_draft(P,draft,data):
    with P.transaction() as cur:
        cur.execute('UPDATE application_drafts SET data=%s,version=version+1,touched_at=%s WHERE id=%s AND mobile=%s AND version=%s AND submitted_application_id IS NULL AND expires_at>%s',
            (json.dumps(data),P.now(),draft['id'],session.get('portal_mobile'),draft['version'],P.now()))
        if cur.rowcount!=1:abort(409,description='This saved draft changed in another tab. Reload and review before continuing.')
    draft['data'],draft['version']=data,draft['version']+1


def ready_errors(P,draft):
    data=draft['data'];values=data.get('form_data',{})
    errors=validate(values,P.now().date())
    if set(data.get('completed',[]))!={1,2,3}:
        errors.setdefault('applicant_name','Complete and save the personal, address and bank steps before review.')
    if not data.get('doc_validated') or not available(data.get('doc_path'),P.UPLOAD_DIR):
        errors['document']='Upload a readable JPG or PDF age-proof document before submitting.'
    return errors


def duplicate_warning(P,draft):
    values=draft['data'].get('form_data',{})
    try:born=parse_dob(values.get('dob',''))
    except ValueError:return False
    with P.transaction() as cur:
        cur.execute('SELECT id FROM applications WHERE mobile=%s AND applicant_name=%s AND dob=%s LIMIT 1',
            (session.get('portal_mobile'),values.get('applicant_name',''),born))
        return cur.fetchone() is not None


def review(P,draft,errors=None,status=200):
    return render_template('declaration.html',data=draft['data'].get('form_data',{}),errors=errors or {},
        submission_key=draft['submission_key'],version=draft['version'],duplicate=duplicate_warning(P,draft),
        document_ready=available(draft['data'].get('doc_path'),P.UPLOAD_DIR),current=5),status


def tracking(P):
    try:page=min(10000,max(1,int(request.args.get('page','1'))))
    except ValueError:abort(400)
    with P.transaction() as cur:
        cur.execute('SELECT id,application_no,applicant_name,status,submitted_at FROM applications WHERE mobile=%s ORDER BY submitted_at DESC,id DESC LIMIT 50 OFFSET %s',
            (session['portal_mobile'],(page-1)*50));applications=cur.fetchall()
        cur.execute('SELECT id,data,kind,application_id FROM application_drafts WHERE mobile=%s AND submitted_application_id IS NULL AND expires_at>%s ORDER BY touched_at DESC LIMIT 20',(session['portal_mobile'],P.now()));drafts=cur.fetchall()
    return render_template('tracking.html',applications=applications,drafts=drafts,page=page)


def install(P):
    app=P.app
    @app.before_request
    def bounded_fields():
        if request.method=='POST' and (any(len(value)>4096 for value in request.form.values()) or sum(len(value) for value in request.form.values())>20000):
            abort(413,description='Form input is too large. Shorten the entered fields; uploaded documents have their separate 5MiB limit.')

    @app.errorhandler(413)
    def too_large(error):
        return render_template('upload.html',errors={'document':'Choose a document up to 5MiB (5,242,880 bytes), and keep other form fields within their displayed limits.'},current=4,data={}),413

    @app.route('/form/<int:step>',methods=['GET','POST'])
    def form_step(step):
        if step not in FIELDS:abort(404)
        draft=draft_record(P)
        if not draft or not session.get('verified_mobile'):
            flash('Verify your contact to continue your saved draft.');return redirect(url_for('apply'))
        if draft['result']:return redirect(url_for('confirmation',app_id=draft['result']))
        if draft['expires_at']<=P.now():
            flash('This unsubmitted draft expired after 7 days. Verify to start a new application.');return redirect(url_for('apply'))
        content=draft['data'];completed=set(content.get('completed',[]))
        missing=next((earlier for earlier in range(1,step) if earlier not in completed),None)
        if missing:
            flash('Complete the earlier saved step before continuing.');return redirect(url_for('form_step',step=missing))
        values=content.get('form_data',{})
        errors=validate(values,P.now().date(),step) if any(field in values for field in FIELDS[step]) else {}
        if request.method=='POST':
            values=dict(values)
            for field in FIELDS[step]:
                if field in request.form:values[field]=request.form[field]
            errors=validate(values,P.now().date(),step)
            completed.discard(step)
            if not errors:completed.add(step)
            content=dict(content,form_data=values,completed=sorted(completed))
            save_draft(P,draft,content)
            if not errors:
                if request.form.get('navigation')=='back' and step>1:return redirect(url_for('form_step',step=step-1),code=303)
                return redirect(url_for('form_step',step=step+1) if step<3 else url_for('upload'),code=303)
        return render_template('form_step'+str(step)+'.html',data=values,blocks=P.BLOCKS,errors=errors,current=step),422 if errors and request.method=='POST' else 200

    @app.route('/upload',methods=['GET','POST'])
    def upload():
        draft=draft_record(P)
        if not draft or not session.get('verified_mobile'):
            return redirect(url_for('apply'))
        if draft['result']:return redirect(url_for('confirmation',app_id=draft['result']))
        errors={};status=200
        if request.method=='POST':
            if request.form.get('keep')=='yes' and draft['data'].get('doc_validated') and available(draft['data'].get('doc_path'),P.UPLOAD_DIR):
                return redirect(url_for('declaration'),code=303)
            try:
                filename=receive(request.files.get('document'),P.UPLOAD_DIR)
                try:save_draft(P,draft,dict(draft['data'],doc_path=filename,doc_validated=True))
                except BaseException:
                    from pathlib import Path
                    Path(filename).unlink(missing_ok=True);raise
                return redirect(url_for('declaration'),code=303)
            except DocumentError as error:errors={'document':error.message};status=error.status
        return render_template('upload.html',errors=errors,current=4,document_ready=available(draft['data'].get('doc_path'),P.UPLOAD_DIR)),status

    @app.route('/declaration',methods=['GET','POST'])
    def declaration():
        draft=draft_record(P)
        if not draft or not session.get('verified_mobile'):return redirect(url_for('apply'))
        if draft['result']:
            if request.method=='POST':return P.handle_submission(draft)
            return redirect(url_for('confirmation',app_id=draft['result']),code=303)
        errors=ready_errors(P,draft)
        if request.method=='POST':
            if request.form.get('declaration')!='yes':errors['declaration']='Confirm the declaration before submitting.'
            if errors:return review(P,draft,errors,422)
            if duplicate_warning(P,draft) and request.form.get('duplicate_ack')!='yes':
                return review(P,draft,{'duplicate_ack':'Check the similar existing record and confirm this is a different eligible applicant.'},422)
            return P.handle_submission(draft)
        if errors:
            for step in (1,2,3):
                if step not in draft['data'].get('completed',[]) or validate(draft['data'].get('form_data',{}),P.now().date(),step):
                    flash('Your saved details are retained. Complete the highlighted step.');return redirect(url_for('form_step',step=step))
            flash('Upload your age proof before final review.');return redirect(url_for('upload'))
        return review(P,draft)

    @app.post('/draft/<ident>/resume')
    def resume_draft(ident):
        if session.get('role')!='citizen':return redirect(url_for('status_login'))
        draft=draft_record(P,ident)
        if not draft or draft['result'] or draft['expires_at']<=P.now():abort(404)
        authenticated=session.get('auth_at')
        if not authenticated or (P.now()-datetime.fromisoformat(authenticated)).total_seconds()>300:
            flash('Request a fresh contact code to resume the saved draft.');return redirect(url_for('status_login'))
        values={'role':'citizen','logged_in':True,'portal_mobile':session['portal_mobile'],'auth_at':authenticated}
        if draft['kind']=='correction':
            values['correction_id']=ident;P.sessions.rotate(values)
            return redirect(url_for('correction_review',app_id=draft['application_id']),code=303)
        values.update(draft_id=ident,verified_mobile=session['portal_mobile']);P.sessions.rotate(values)
        return redirect(url_for('form_step',step=1),code=303)

    @app.get('/draft/document')
    def draft_document():
        draft=draft_record(P)
        if not draft or not session.get('verified_mobile'):abort(403)
        filename=draft['data'].get('doc_path')
        if not available(filename,P.UPLOAD_DIR):abort(404)
        return send_file(filename,as_attachment=True,download_name='age-proof'+__import__('pathlib').Path(filename).suffix,conditional=False)

    @app.get('/document/<int:app_id>')
    def document(app_id):
        if not P.authorize_record(app_id):abort(403)
        with P.transaction() as cur:
            cur.execute('SELECT doc_path FROM applications WHERE id=%s',(app_id,));filename=cur.fetchone()[0]
        if not available(filename,P.UPLOAD_DIR):abort(404,description='No document is available in this record. Your application remains valid.')
        return send_file(filename,as_attachment=True,download_name='age-proof'+__import__('pathlib').Path(filename).suffix,conditional=False)
