"""Citizen barriers, owner corrections and preserved state on real PostgreSQL."""
from datetime import timedelta
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import pytest
from citizen_support import (citizen,verified,pdf_bytes,jpg_bytes,upload,submit,existing,
 correction_review,prove_correction,confirm,prepare,post_form,query,form_fields,rows,text)


def test_widow_can_apply_without_deceased_spouse_employer(citizen):
    ctx=citizen;client=ctx.portal.app.test_client();prepare(client,ctx.mobile)
    post_form(client,'/form/1',{'applicant_name':'Synthetic Widow','dob':'13/12/1950','gender':'Female',
        'marital_status':'Widowed','husband_name':'Synthetic Deceased','husband_employer':''})
    response=submit(client)
    assert response.status_code==200 and len(rows(ctx.database,ctx.mobile))==1,'Irrelevant deceased-spouse employer excluded a widow'


def test_distinct_adults_can_share_one_contact(citizen):
    ctx=citizen
    first=ctx.portal.app.test_client();prepare(first,ctx.mobile,name='Synthetic Adult One');submit(first)
    assert len(rows(ctx.database,ctx.mobile))==1
    ctx.clock.instant+=timedelta(seconds=61)
    second=ctx.portal.app.test_client();prepare(second,ctx.mobile,name='Synthetic Adult Two');response=submit(second)
    assert response.status_code==200 and len(rows(ctx.database,ctx.mobile))==2,'Mobile contact was treated as a unique person'
    assert query(ctx,'SELECT count(*) FROM portal_users WHERE mobile=%s',(ctx.mobile,))==[(1,)]


@pytest.mark.parametrize('kind',['jpg','pdf'])
def test_advertised_valid_upload_above_old_100k_limit(citizen,kind):
    ctx=citizen;client=verified(ctx)
    raw=jpg_bytes() if kind=='jpg' else pdf_bytes(150*1024)
    assert 102400<len(raw)<=5242880
    response=upload(client,raw,'proof.'+kind,'image/jpeg' if kind=='jpg' else 'application/pdf')
    assert response.status_code in (302,303),'Valid advertised document was rejected'


@pytest.mark.parametrize('raw,filename,mime',[
 (b'%PDF-1.7\nnot a document','proof.pdf','application/pdf'),
 (b'not a JPEG','proof.jpg','image/jpeg'),
 (b'not an image','proof.pdf','image/jpeg'),
])
def test_malformed_or_mismatched_upload_is_actionable(citizen,raw,filename,mime):
    client=verified(citizen)
    response=upload(client,raw,filename,mime)
    assert response.status_code in (400,413,415,422),'Malformed content was accepted'
    assert b'ERR_VAL_47' not in response.data and b'document' in response.data.lower()


def test_real_jpeg_cannot_masquerade_as_pdf(citizen):
    response=upload(verified(citizen),jpg_bytes(),'proof.pdf','application/pdf')
    assert response.status_code in (400,415,422)


@pytest.mark.parametrize('size,expected',[(5242880,True),(5242881,False)])
def test_exact_advertised_upload_boundary(citizen,size,expected):
    response=upload(verified(citizen),pdf_bytes(size),'proof.pdf','application/pdf')
    assert (response.status_code in (302,303)) is expected,'File-size rule differs from advertised5MiB'
    if not expected:assert response.status_code==413


def test_generated_upload_path_cannot_escape_private_root(citizen):
    # Already fixed by the access prerequisite at e3bbda3, not a newly claimed red failure.
    client=verified(citizen)
    response=upload(client,pdf_bytes(),'../../escape.pdf','application/pdf')
    assert response.status_code in (200,302,303,400,422)
    from pathlib import Path
    assert not (Path(citizen.portal.UPLOAD_DIR).parent/'escape.pdf').exists()


def test_skipped_steps_do_not_open_later_form(citizen):
    client=verified(citizen)
    assert client.get('/form/3').status_code in (302,303,403,409),'Uncompleted steps were silently skipped'


@pytest.mark.parametrize('declaration',[None,'','no'])
def test_declaration_is_required_on_server(citizen,declaration):
    ctx=citizen;client=ctx.portal.app.test_client();prepare(client,ctx.mobile)
    fields=form_fields(client.get('/declaration'))
    if declaration is not None:fields['declaration']=declaration
    response=client.post('/declaration',data=fields,follow_redirects=True)
    assert rows(ctx.database,ctx.mobile)==[],'Missing declaration still created an application'
    assert b'declaration' in response.data.lower()


@pytest.mark.parametrize('step,field,value',[(1,'dob','31/02/1950'),(2,'block','Synthetic Unknown Block'),
 (3,'bank_account','12abc'),(3,'ifsc','INVALID')])
def test_invalid_fields_have_linked_errors_and_retain_input(citizen,step,field,value):
    ctx=citizen;client=ctx.portal.app.test_client();prepare(client,ctx.mobile)
    response=post_form(client,f'/form/{step}',{field:value})
    assert response.status_code==422,'Invalid input advanced to another step'
    html=text(response)
    assert value in html and ('href="#'+field+'"') in html,'Field error lost its input or label target'
    reloaded=text(client.get(f'/form/{step}'))
    assert value in reloaded and ('href="#'+field+'"') in reloaded,'Reload lost saved invalid input or actionable errors'


def test_back_navigation_retains_saved_values(citizen):
    ctx=citizen;client=ctx.portal.app.test_client();prepare(client,ctx.mobile,name='Synthetic Retained')
    response=client.get('/form/2')
    assert b'/form/1' in response.data,'No discoverable back path'
    assert b'Synthetic Retained' in client.get('/form/1').data
    assert b'000012345678' in client.get('/form/3').data


def test_pending_owner_can_review_correct_with_audit_and_preserve_sla(citizen):
    ctx=citizen;client,ident,before=existing(ctx)
    ctx.clock.instant+=timedelta(seconds=61)
    review=correction_review(ctx,client,ident,{'bank_account':'000077778888'})
    review=prove_correction(ctx,client,ident,review)
    assert confirm(client,ident,review).status_code in (302,303)
    after=query(ctx,'SELECT to_jsonb(a) FROM applications a WHERE id=%s',(ident,))[0][0]
    assert after['bank_account']=='000077778888'
    for field in ('id','application_no','mobile','status','submitted_at','decided_at','decided_by'):
        assert after[field]==before[field], 'Correction rewrote identity, decision or original SLA'
    audits=query(ctx,"SELECT changed_values FROM application_audit WHERE application_id=%s AND event='CORRECTED'",(ident,))
    assert len(audits)==1 and '000012345678' in str(audits[0]) and '000077778888' in str(audits[0])
    assert b'000077778888' in client.get(f'/application/{ident}').data


def test_sensitive_correction_without_fresh_proof_cannot_commit(citizen):
    ctx=citizen;client,ident,before=existing(ctx)
    review=correction_review(ctx,client,ident,{'bank_account':'000077778888'})
    assert confirm(client,ident,review).status_code in (302,303,403,409)
    assert query(ctx,'SELECT bank_account FROM applications WHERE id=%s',(ident,))==[(before['bank_account'],)]


def test_other_contact_cannot_open_or_post_correction(citizen):
    ctx=citizen;owner,ident,before=existing(ctx)
    stranger=verified(ctx,'9300000001')
    assert stranger.get(f'/application/{ident}/edit').status_code in (302,303,403,404)
    assert post_form(stranger,f'/application/{ident}/edit',{'bank_account':'00001111'},page='/status').status_code in (302,303,403,404)
    assert query(ctx,'SELECT bank_account FROM applications WHERE id=%s',(ident,))==[(before['bank_account'],)]


@pytest.mark.parametrize('decision',['APPROVED','REJECTED','DEEMED_APPROVED'])
def test_decided_record_cannot_self_correct(citizen,decision):
    ctx=citizen;client,ident,before=existing(ctx)
    query(ctx,'UPDATE applications SET status=%s WHERE id=%s',(decision,ident))
    response=post_form(client,f'/application/{ident}/edit',{'bank_account':'00009999'},page='/status')
    assert response.status_code in (403,404,409)
    assert query(ctx,'SELECT bank_account,status FROM applications WHERE id=%s',(ident,))==[(before['bank_account'],decision)]


@pytest.mark.parametrize('decision',['APPROVED','DEEMED_APPROVED'])
def test_correction_loses_real_row_lock_race_to_committed_decision(citizen,decision):
    ctx=citizen;client,ident,before=existing(ctx);ctx.clock.instant+=timedelta(seconds=61)
    review=prove_correction(ctx,client,ident,correction_review(ctx,client,ident,{'bank_account':'000077778888'}))
    with closing(ctx.database.connect()) as manual:
        with manual.cursor() as cursor:
            cursor.execute('UPDATE applications SET status=%s WHERE id=%s',(decision,ident))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(confirm,client,ident,review)
            deadline=time.monotonic()+3
            try:
                while time.monotonic()<deadline:
                    waiting=query(ctx,"SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' AND datname=current_database()")
                    if waiting[0][0]:break
                    assert not future.done(),'Correction did not attempt a protected database transition'
                    time.sleep(.02)
                else:pytest.fail('Did not observe real PostgreSQL correction/decision lock race')
            finally:manual.commit()
            assert future.result(timeout=5).status_code in (403,409)
    assert query(ctx,'SELECT bank_account,status,submitted_at FROM applications WHERE id=%s',(ident,))==[(before['bank_account'],decision,__import__('datetime').datetime.fromisoformat(before['submitted_at']))]


def test_historic_absent_document_is_honest_state(citizen):
    ctx=citizen;client,ident,before=existing(ctx)
    query(ctx,"UPDATE applications SET doc_path='' WHERE id=%s",(ident,))
    response=client.get(f'/application/{ident}')
    assert response.status_code==200 and b'no document' in response.data.lower(),'Absent historical proof was not explained'


def test_form_controls_have_programmatic_labels(citizen):
    from html.parser import HTMLParser
    class Audit(HTMLParser):
        def __init__(self):super().__init__();self.labels=set();self.fields=[]
        def handle_starttag(self,tag,attrs):
            a=dict(attrs)
            if tag=='label' and a.get('for'):self.labels.add(a['for'])
            if tag in ('input','select','textarea') and a.get('type') not in ('hidden','submit'):self.fields.append(a.get('id'))
    ctx=citizen;client=ctx.portal.app.test_client();prepare(client,ctx.mobile)
    for path in ('/form/1','/form/2','/form/3','/upload','/declaration'):
        audit=Audit();audit.feed(text(client.get(path)))
        assert audit.fields and all(value and value in audit.labels for value in audit.fields),'Unlabelled controls prevent keyboard/assistive use'


def test_replaced_otp_cannot_verify_new_challenge(citizen,monkeypatch):
    # Purpose/consumption infrastructure was fixed at e3bbda3; this is a preservation guard.
    from security_support import application_challenge
    from submission_support import otp
    import secrets
    sequence=iter([1,2]);monkeypatch.setattr(secrets,'randbelow',lambda limit:next(sequence))
    ctx=citizen;client=ctx.portal.app.test_client()
    application_challenge(ctx,client,mobile=ctx.mobile);old=otp(client,ctx.mobile)
    ctx.clock.instant+=timedelta(seconds=61)
    application_challenge(ctx,client,mobile=ctx.mobile);new=otp(client,ctx.mobile)
    post_form(client,'/verify',{'otp':old})
    assert client.get('/form/1').status_code in (302,303,403)
    post_form(client,'/verify',{'otp':new})
    assert client.get('/form/1').status_code==200


def test_slow_completion_and_same_submit_preserve_one_intent(citizen):
    # Session lifetime e3bbda3 and idempotency7c6327a are established guards, not fabricated new reds.
    ctx=citizen;client=ctx.portal.app.test_client();data=prepare(client,ctx.mobile)
    ctx.clock.instant+=timedelta(minutes=10)
    assert client.get('/form/3').status_code==200
    one=client.post('/declaration',data=data,follow_redirects=True)
    two=client.post('/declaration',data=data,follow_redirects=True)
    persisted=rows(ctx.database,ctx.mobile)
    assert len(persisted)==1 and persisted[0][1].encode() in one.data and persisted[0][1].encode() in two.data


def test_expired_draft_cleanup_keeps_accepted_attachment(citizen):
    from pathlib import Path
    from conftest import ROOT,load_module
    import os
    ctx=citizen;client,ident,before=existing(ctx)
    accepted_file=Path(before['doc_path'])
    waiting=ctx.portal.app.test_client();prepare(waiting,'9300000001',name='Synthetic Waiting')
    query(ctx,"UPDATE application_drafts SET expires_at=now()-interval '8 days' WHERE mobile='9300000001'")
    pending=query(ctx,"SELECT data->>'doc_path' FROM application_drafts WHERE mobile='9300000001'")[0][0]
    old=time.time()-9*86400
    os.utime(pending,(old,old));os.utime(accepted_file,(old,old))
    cleanup=load_module('citizen_cleanup',ROOT/'app/scripts/cleanup.py')
    error_kind=None
    try:cleanup.main()
    except Exception as error:error_kind=type(error).__name__
    assert query(ctx,"SELECT count(*) FROM application_drafts WHERE mobile='9300000001'")==[(0,)],'Expired draft remains; supporting entry error: '+str(error_kind)
    assert accepted_file.exists() and not Path(pending).exists(),'Cleanup deleted accepted evidence or retained orphan proof'


def test_correction_rejects_mass_assignment_of_contact_and_decision(citizen):
    ctx=citizen;client,ident,before=existing(ctx)
    assert client.get(f'/application/{ident}/edit').status_code==200
    response=post_form(client,f'/application/{ident}/edit',{'status':'APPROVED','mobile':'9300000099',
        'submitted_at':'2026-10-24','application_no':'ALTERED','id':'99','bank_account':'000055556666'})
    assert response.status_code in (400,403,422)
    assert query(ctx,'SELECT to_jsonb(a) FROM applications a WHERE id=%s',(ident,))[0][0]==before


def test_editing_reviewed_sensitive_values_invalidates_stepup(citizen):
    ctx=citizen;client,ident,before=existing(ctx);ctx.clock.instant+=timedelta(seconds=61)
    original=prove_correction(ctx,client,ident,correction_review(ctx,client,ident,{'bank_account':'000077778888'}))
    correction_review(ctx,client,ident,{'bank_account':'000055556666'})
    assert confirm(client,ident,original).status_code in (302,303,403,409)
    assert query(ctx,'SELECT bank_account FROM applications WHERE id=%s',(ident,))==[(before['bank_account'],)]


def test_concurrent_review_version_cannot_overwrite_first_correction(citizen):
    ctx=citizen;first,ident,before=existing(ctx);ctx.clock.instant+=timedelta(seconds=61)
    second=verified(ctx)
    a=correction_review(ctx,first,ident,{'bank_account':'000077778888'})
    b=correction_review(ctx,second,ident,{'bank_account':'000055556666'})
    ctx.clock.instant+=timedelta(seconds=61);a=prove_correction(ctx,first,ident,a)
    ctx.clock.instant+=timedelta(seconds=61);b=prove_correction(ctx,second,ident,b)
    assert confirm(first,ident,a).status_code in (302,303)
    assert confirm(second,ident,b).status_code==409
    assert query(ctx,'SELECT bank_account FROM applications WHERE id=%s',(ident,))==[('000077778888',)]
    assert query(ctx,"SELECT count(*) FROM application_audit WHERE application_id=%s AND event='CORRECTED'",(ident,))==[(1,)]


def test_correction_refreshes_receipt_without_reapplying(citizen):
    import io
    from pypdf import PdfReader
    ctx=citizen;client,ident,before=existing(ctx)
    old=client.get(f'/ack/{ident}.pdf');assert old.status_code==200
    ctx.clock.instant+=timedelta(seconds=61)
    review=prove_correction(ctx,client,ident,correction_review(ctx,client,ident,{'bank_account':'000077778888'}))
    assert confirm(client,ident,review).status_code in (302,303)
    new=client.get(f'/ack/{ident}.pdf');assert new.status_code==200 and new.data!=old.data
    content=' '.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(new.data)).pages)
    assert '000077778888' in content and '000012345678' not in content
    assert len(rows(ctx.database,ctx.mobile))==1


def test_similar_person_warning_allows_a_distinct_adult_with_same_details(citizen):
    ctx=citizen;first,ident,before=existing(ctx);ctx.clock.instant+=timedelta(seconds=61)
    second=ctx.portal.app.test_client();data=prepare(second,ctx.mobile)
    assert b'similar' in second.get('/declaration').data.lower() or b'already linked' in second.get('/declaration').data.lower()
    response=second.post('/declaration',data=data)
    assert response.status_code==422 and len(rows(ctx.database,ctx.mobile))==1
    response=second.post('/declaration',data=data|{'duplicate_ack':'yes'},follow_redirects=True)
    assert response.status_code==200 and len(rows(ctx.database,ctx.mobile))==2


def test_pending_draft_can_resume_after_intake_closes_but_cannot_be_accepted(citizen):
    from datetime import datetime
    from submission_support import login
    ctx=citizen;ctx.clock.instant=datetime.fromisoformat('2026-10-31T12:00:00+05:30')
    first=ctx.portal.app.test_client();prepare(first,ctx.mobile,name='Synthetic Saved Closure')
    ident=query(ctx,'SELECT id FROM application_drafts WHERE mobile=%s',(ctx.mobile,))[0][0]
    ctx.clock.instant=datetime.fromisoformat('2026-11-01T00:00:01+05:30')
    fresh=ctx.portal.app.test_client();login(fresh,ctx.mobile)
    response=post_form(fresh,f'/draft/{ident}/resume',page='/status')
    assert response.status_code in (302,303)
    assert b'Synthetic Saved Closure' in fresh.get('/form/1').data
    submit(fresh)
    assert rows(ctx.database,ctx.mobile)==[]


def test_replaced_accepted_document_is_retained_as_private_audit_evidence(citizen):
    import io
    from pathlib import Path
    from conftest import ROOT,load_module
    import os
    ctx=citizen;client,ident,before=existing(ctx);ctx.clock.instant+=timedelta(seconds=61)
    page=client.get(f'/application/{ident}/edit')
    response=client.post(f'/application/{ident}/edit',data=form_fields(page)|{'document':(io.BytesIO(jpg_bytes()),'proof.jpg','image/jpeg')},follow_redirects=True)
    assert response.status_code==200
    review=prove_correction(ctx,client,ident,response)
    assert confirm(client,ident,review).status_code in (302,303)
    current=query(ctx,'SELECT doc_path FROM applications WHERE id=%s',(ident,))[0][0]
    assert current!=before['doc_path'] and client.get(f'/document/{ident}').data.startswith(b'\xff\xd8')
    old=time.time()-9*86400;os.utime(before['doc_path'],(old,old))
    load_module('audit_proof_cleanup',ROOT/'app/scripts/cleanup.py').main()
    assert Path(before['doc_path']).exists() and Path(current).exists(),'Accepted evidence was deleted after a correction'
