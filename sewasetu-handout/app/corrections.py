"""Owner-bound pending corrections: reviewed values, step-up proof and row-version guards."""
import hashlib
import json
import secrets
from datetime import date,datetime,timedelta
from flask import request,session,redirect,url_for,render_template,flash,abort
from forms import FIELDS,LABELS,validate,parse_dob
from documents import receive,DocumentError
from access import issue,consume
from notifications import enqueue
from citizen_routes import draft_record

EDITABLE=tuple(field for fields in FIELDS.values() for field in fields)+('doc_path',)
SENSITIVE=set(FIELDS[1])|{'bank_account','ifsc','doc_path'}


def normal(value):
    return value.isoformat() if isinstance(value,(date,datetime)) else value


def application(P,ident,cur=None,lock=False):
    def read(cursor):
        cursor.execute('SELECT * FROM applications WHERE id=%s'+(' FOR UPDATE' if lock else''),(ident,))
        row=cursor.fetchone()
        return dict(zip((column.name for column in cursor.description),row)) if row else None
    if cur:return read(cur)
    with P.transaction() as cursor:return read(cursor)


def owner(record):
    if session.get('role')!='citizen' or not record or record['mobile']!=session.get('portal_mobile'):abort(403)


def pending(record):
    if record['status']!='PENDING':abort(409,description='Self-service correction is unavailable after a decision. A verified assisted correction channel has not been supplied.')


def active(P,ident):
    draft=draft_record(P,session.get('correction_id'))
    if not draft or draft['kind']!='correction' or draft['application_id']!=ident or draft['expires_at']<=P.now():return None
    return draft


def fields(record):
    values={key:record.get(key) or'' for key in EDITABLE}
    values['dob']=record['dob'].strftime('%d/%m/%Y') if record.get('dob') else''
    return values


def proposal_digest(ident,version,changes):
    return hashlib.sha256(json.dumps({'application':ident,'version':version,'changes':changes},sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def purpose(ident,draft):
    return 'correction:'+str(ident)+':'+str(draft['data']['baseline_version'])+':'+draft['data']['digest']


def install(P):
    app=P.app
    @app.route('/application/<int:app_id>/edit',methods=['GET','POST'])
    def correction_edit(app_id):
        record=application(P,app_id);owner(record);pending(record)
        current=active(P,app_id)
        if request.args.get('reload')=='1':session.pop('correction_id',None);current=None
        values=dict(current['data']['values']) if current and not current['result'] else fields(record)
        errors={}
        if request.method=='POST':
            unknown=set(request.form)-set(EDITABLE)-{'csrf_token','version','navigation'}
            if 'doc_path' in request.form:unknown.add('doc_path')
            for field in EDITABLE:
                if field!='doc_path' and field in request.form:values[field]=request.form[field]
            if unknown:errors['correction-form']='Only the editable fields can be changed; IDs, contact, dates and decisions are protected.'
            try:version=int(request.form.get('version',''))
            except ValueError:abort(400)
            if version!=record['receipt_version']:abort(409,description='This record changed. Reload the latest version and review your correction again.')
            new_file=None
            try:
                if request.files.get('document') and request.files['document'].filename:
                    new_file=receive(request.files['document'],P.UPLOAD_DIR);values['doc_path']=new_file
            except DocumentError as error:errors['document']=error.message
            changes={}
            updated={}
            for key in EDITABLE:
                value=values.get(key,'')
                if key=='dob':
                    try:value=parse_dob(value)
                    except ValueError:pass
                if record.get(key) is None and value=='':value=None
                if normal(value)!=normal(record.get(key)):
                    changes[key]={'before':normal(record.get(key)),'after':normal(value)};updated[key]=value
            validation=validate(values,record['submitted_at'].date())
            errors.update({key:message for key,message in validation.items() if key in changes})
            if not changes:errors['correction-form']='Change at least one editable value before reviewing.'
            with P.transaction() as cur:
                cur.execute("SELECT changed_values->'_identity_origin' FROM application_audit WHERE application_id=%s AND event='CORRECTED' ORDER BY id LIMIT 1",(app_id,))
                previous=cur.fetchone()
            origin=previous[0] if previous and previous[0] else {'applicant_name':record['applicant_name'],'dob':normal(record['dob'])}
            if values.get('applicant_name')!=origin['applicant_name'] and normal(updated.get('dob',record['dob']))!=origin['dob']:
                errors['applicant_name']='Self-service cannot replace both original identity details. Assisted verification is required; no verified channel has been supplied.'
            if errors:
                if new_file:__import__('pathlib').Path(new_file).unlink(missing_ok=True)
                return render_template('correction_edit.html',data=values,errors=errors,blocks=P.BLOCKS,version=version,app_id=app_id),422
            content={'values':values,'original':{key:normal(record.get(key)) for key in EDITABLE},'changes':changes,
                'baseline_version':version,'digest':proposal_digest(app_id,version,changes),'identity_origin':origin,
                'requires_stepup':bool(set(changes)&SENSITIVE)}
            ident=current['id'] if current and not current['result'] else secrets.token_urlsafe(24)
            with P.transaction() as cur:
                if current and not current['result']:
                    cur.execute('UPDATE application_drafts SET data=%s,proof_at=NULL,touched_at=%s,version=version+1 WHERE id=%s AND mobile=%s',
                        (json.dumps(content),P.now(),ident,session['portal_mobile']))
                else:
                    cur.execute("INSERT INTO application_drafts(id,mobile,data,touched_at,expires_at,submission_key,kind,application_id) VALUES(%s,%s,%s,%s,%s,%s,'correction',%s)",
                        (ident,session['portal_mobile'],json.dumps(content),P.now(),P.now()+timedelta(days=7),ident,app_id))
            session['correction_id']=ident;session.pop('correction_challenge',None)
            return redirect(url_for('correction_review',app_id=app_id),code=303)
        return render_template('correction_edit.html',data=values,errors=errors,blocks=P.BLOCKS,version=record['receipt_version'],app_id=app_id)

    @app.get('/application/<int:app_id>/edit/review')
    def correction_review(app_id):
        record=application(P,app_id);owner(record);pending(record)
        draft=active(P,app_id)
        if not draft:return redirect(url_for('correction_edit',app_id=app_id))
        if draft['result']:return redirect(url_for('view_application',app_id=app_id))
        return render_template('correction_review.html',app_id=app_id,proposal=draft['data'],labels=LABELS,
            proved=bool(draft['proof_at'] and (P.now()-draft['proof_at']).total_seconds()<300),errors={})

    @app.post('/application/<int:app_id>/edit/request-code')
    def correction_code(app_id):
        record=application(P,app_id);owner(record);pending(record)
        draft=active(P,app_id)
        if not draft or draft['result']:abort(409)
        challenge=issue(P.transaction,P.now,P.SETTINGS,record['mobile'],purpose(app_id,draft),P.send_sms)
        if not challenge:
            flash('Wait before requesting another code, or retry when SMS delivery returns. Your reviewed values are saved.')
            return redirect(url_for('correction_review',app_id=app_id),code=303)
        session['correction_challenge']=challenge
        return redirect(url_for('correction_verify',app_id=app_id),code=303)

    @app.route('/application/<int:app_id>/edit/verify',methods=['GET','POST'])
    def correction_verify(app_id):
        record=application(P,app_id);owner(record);pending(record)
        draft=active(P,app_id)
        if not draft or draft['result']:abort(409)
        if request.method=='POST':
            mobile=consume(P.transaction,P.now,P.SETTINGS,session.get('correction_challenge',''),purpose(app_id,draft),request.form.get('otp',''))
            if mobile==record['mobile']:
                with P.transaction() as cur:
                    cur.execute("UPDATE application_drafts SET proof_at=%s WHERE id=%s AND data->>'digest'=%s",
                        (P.now(),draft['id'],draft['data']['digest']))
                values=dict(session);values.pop('csrf_token',None);values.pop('correction_challenge',None)
                values['auth_at']=P.now().isoformat();P.sessions.rotate(values)
                return redirect(url_for('correction_review',app_id=app_id),code=303)
            flash('This code expired, was replaced, or is incorrect. Request a new code; the reviewed values are retained.')
        return render_template('verify.html',mobile=record['mobile'],correction_id=app_id)

    @app.post('/application/<int:app_id>/edit/confirm')
    def correction_confirm(app_id):
        # Always acquire application then proposal locks, consistently with decisions.
        with P.transaction() as cur:
            record=application(P,app_id,cur,lock=True);owner(record)
            cur.execute("SELECT data,proof_at,submitted_application_id,expires_at FROM application_drafts WHERE id=%s AND mobile=%s AND kind='correction' AND application_id=%s FOR UPDATE",
                (session.get('correction_id'),session.get('portal_mobile'),app_id))
            row=cur.fetchone()
            if not row:abort(403)
            if row[2]:return redirect(url_for('view_application',app_id=app_id),code=303)
            pending(record)
            proposal,proof,_,expiry=row
            if expiry<=P.now():abort(409,description='This reviewed correction expired. Reopen the record to prepare it again.')
            if request.form.get('review_digest')!=proposal['digest'] or request.form.get('version')!=str(proposal['baseline_version']):abort(409)
            if record['receipt_version']!=proposal['baseline_version'] or {key:normal(record.get(key)) for key in EDITABLE}!=proposal['original']:
                abort(409,description='The record changed after review. Reload the latest details; no correction was applied.')
            if proposal['requires_stepup'] and (not proof or (P.now()-proof).total_seconds()>=300):abort(403)
            changed=list(proposal['changes'])
            if not changed or not set(changed)<=set(EDITABLE):abort(403)
            values=[]
            for field in changed:
                value=proposal['values'][field]
                if field=='dob':value=parse_dob(value)
                values.append(value)
            cur.execute('UPDATE applications SET '+','.join(key+'=%s' for key in changed)+",receipt_version=receipt_version+1 WHERE id=%s AND status='PENDING' AND receipt_version=%s RETURNING receipt_version",
                tuple(values)+(app_id,proposal['baseline_version']))
            version=cur.fetchone()[0]
            evidence=dict(proposal['changes'],_identity_origin=proposal['identity_origin'])
            cur.execute("INSERT INTO application_audit(application_id,event,actor,occurred_at,changed_values) VALUES(%s,'CORRECTED',%s,%s,%s)",
                (app_id,'citizen:'+record['mobile'],P.now(),json.dumps(evidence)))
            enqueue(cur,app_id,'CORRECTED-v'+str(version),record['mobile'],'Sewa Setu: correction saved for application '+record['application_no']+'. Original decision deadline is unchanged.',P.now())
            cur.execute("UPDATE application_drafts SET submitted_application_id=%s,data='{}',proof_at=NULL WHERE id=%s",(app_id,session['correction_id']))
        flash('Correction saved. Your original application number and decision deadline are unchanged.')
        return redirect(url_for('view_application',app_id=app_id),code=303)
