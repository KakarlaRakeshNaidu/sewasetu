"""Accepted Unicode and deterministic date-only identity through every retained layer."""
import hashlib
import io
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from citizen_support import citizen,verified,prepare,submit,post_form,query,rows
from submission_support import login


NAMES=['श्रीमती कमला देवी शर्मा','শ্ৰীমতী জোনালী বৰা','শ্রীমতী নন্দিতা চক্রবর্তী','A\u0301sha Kumari Devi Sharma']


def set_identity(client,name,dob):
    return post_form(client,'/form/1',{'applicant_name':name,'dob':dob,'gender':'Female','marital_status':'Unmarried'})


@pytest.mark.parametrize('name',NAMES)
def test_accepted_name_survives_db_confirmation_tracking_admin_and_receipt(citizen,name):
    ctx=citizen;client=ctx.portal.app.test_client();prepare(client,ctx.mobile)
    assert set_identity(client,name,'13/12/1950').status_code in (302,303)
    response=submit(client)
    assert response.status_code==200
    stored=query(ctx,'SELECT id,applicant_name,dob::text,bank_account FROM applications WHERE mobile=%s',(ctx.mobile,))
    assert len(stored)==1 and stored[0][1]==name,'Accepted name was truncated or altered'
    ident=stored[0][0]
    assert name.encode() in response.data,'Confirmation omitted the accepted identity'
    detail=client.get(f'/application/{ident}')
    assert name.encode() in detail.data and b'13/12/1950' in detail.data
    staff=ctx.portal.app.test_client()
    assert post_form(staff,'/admin',{'username':ctx.portal.ADMIN_USERNAME,'password':'Synthetic-Only-Staff-Password'}).status_code in (302,303)
    assert name.encode() in staff.get(f'/admin/application/{ident}').data
    receipt=client.get(f'/ack/{ident}.pdf')
    assert receipt.status_code==200 and receipt.data.startswith(b'%PDF-')
    from pypdf import PdfReader
    content='\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(receipt.data)).pages)
    assert name in content and '13/12/1950' in content,'Receipt content lost accepted identity'
    assert stored[0][2:] == ('1950-12-13','000012345678')


def test_ambiguous_display_dob_means_four_december_not_april(citizen):
    ctx=citizen;client=ctx.portal.app.test_client();prepare(client,ctx.mobile)
    set_identity(client,'Synthetic Ambiguous','04/12/1957');response=submit(client)
    assert response.status_code==200
    assert query(ctx,'SELECT dob::text,to_jsonb(dob) FROM applications WHERE mobile=%s',(ctx.mobile,))==[('1957-12-04','1957-12-04')],'DD/MM/YYYY was interpreted using a US fallback'


@pytest.mark.parametrize('dob',['31/02/1950','29/02/1957','01/01/2030','1950-12-13','4/12/1957','12/4/1957','04-12-1957'])
def test_invalid_future_or_noncanonical_dob_never_accepts(citizen,dob):
    ctx=citizen;client=ctx.portal.app.test_client();prepare(client,ctx.mobile)
    set_identity(client,'Synthetic Invalid Date',dob);submit(client)
    assert rows(ctx.database,ctx.mobile)==[],'Invalid/future/noncanonical DOB was accepted'


@pytest.mark.parametrize('instant,dob,accepted',[
 ('2026-10-24T12:00:00+05:30','24/10/1966',True),
 ('2026-10-24T12:00:00+05:30','25/10/1966',False),
 ('2024-02-29T12:00:00+05:30','29/02/1964',True),
 ('2024-02-28T12:00:00+05:30','29/02/1964',False),
])
def test_exact_sixtieth_birthday_and_leap_boundaries(citizen,instant,dob,accepted):
    ctx=citizen;ctx.clock.instant=datetime.fromisoformat(instant)
    client=ctx.portal.app.test_client();prepare(client,ctx.mobile)
    set_identity(client,'Synthetic Age Boundary',dob);submit(client)
    assert bool(rows(ctx.database,ctx.mobile)) is accepted


@pytest.mark.parametrize('instant,accepted',[
 ('2026-10-31T23:59:59+05:30',True),('2026-11-01T00:00:00+05:30',False),('2026-11-01T00:00:01+05:30',False)])
def test_actual_submission_exclusive_ist_midnight_with_utc_server(citizen,instant,accepted):
    # Exclusive boundary infrastructure already fixed in e3bbda3; this is actual final-submit preservation.
    ctx=citizen;ctx.clock.instant=datetime.fromisoformat('2026-10-31T23:50:00+05:30')
    client=ctx.portal.app.test_client();data=prepare(client,ctx.mobile)
    old=os.environ.get('TZ')
    try:
        os.environ['TZ']='UTC';time.tzset();ctx.clock.server_zone=ZoneInfo('UTC')
        ctx.clock.instant=datetime.fromisoformat(instant)
        client.post('/declaration',data=data,follow_redirects=True)
        assert bool(rows(ctx.database,ctx.mobile)) is accepted
        for path in ['/status','/admin','/__gateway/']:
            assert client.get(path).status_code==200
    finally:
        if old is None:os.environ.pop('TZ',None)
        else:os.environ['TZ']=old
        time.tzset()


def test_overlong_identity_is_rejected_instead_of_silently_cut(citizen):
    ctx=citizen;client=ctx.portal.app.test_client();prepare(client,ctx.mobile)
    set_identity(client,'क'*101,'13/12/1950');submit(client)
    assert rows(ctx.database,ctx.mobile)==[],'Overlong identity was silently truncated and accepted'


def test_existing_synthetic_indic_receipt_requires_shaped_unicode_font(citizen):
    ctx=citizen;client=verified(ctx)
    name='श्रीमती कमला देवी शर्मा / শ্ৰীমতী জোনালী বৰা'
    ident=query(ctx,"""INSERT INTO applications(application_no,applicant_name,mobile,dob,bank_account,status,submitted_at)
        VALUES('SYNTH-INDIC-RECEIPT',%s,%s,'1950-12-13','000012345678','PENDING','2026-10-01') RETURNING id""",(name,ctx.mobile))[0][0]
    response=client.get(f'/ack/{ident}.pdf')
    assert response.status_code==200,'Indic receipt could not be rendered'
    from pypdf import PdfReader
    content='\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(response.data)).pages)
    assert name in content and '?' not in content,'Receipt replaced Indic identity rather than preserving it'


def test_historic_synthetic_identity_and_legacy_account_stay_exact(citizen):
    ctx=citizen
    password_hash=hashlib.sha256(b'12041957').hexdigest()
    ident=query(ctx,"""INSERT INTO applications(application_no,applicant_name,mobile,dob,bank_account,status,submitted_at)
      VALUES('SYNTH-HISTORIC','Synthetic Historic','9400000000','1957-04-12','000000123456','APPROVED','2026-01-01') RETURNING id""")[0][0]
    query(ctx,'INSERT INTO portal_users(mobile,password_hash) VALUES(%s,%s)',('9400000000',password_hash))
    before=query(ctx,'SELECT to_jsonb(a) FROM applications a WHERE id=%s',(ident,))[0][0]
    newcomer=ctx.portal.app.test_client();prepare(newcomer,ctx.mobile);submit(newcomer)
    legacy=ctx.portal.app.test_client();assert login(legacy,'9400000000').status_code in (302,303)
    assert legacy.get(f'/application/{ident}').status_code==200
    assert query(ctx,'SELECT to_jsonb(a) FROM applications a WHERE id=%s',(ident,))[0][0]==before,'Historic identity was guessed or rewritten'
    assert query(ctx,'SELECT password_hash FROM portal_users WHERE mobile=%s',('9400000000',))==[(password_hash,)]


def test_sixtieth_birthday_uses_ist_date_just_after_midnight_on_utc_host(citizen):
    ctx=citizen;ctx.clock.instant=datetime.fromisoformat('2026-10-31T00:00:01+05:30')
    ctx.clock.server_zone=ZoneInfo('UTC')
    client=ctx.portal.app.test_client();prepare(client,ctx.mobile)
    set_identity(client,'Synthetic Midnight Birthday','31/10/1966');submit(client)
    assert len(rows(ctx.database,ctx.mobile))==1,'UTC server date excluded an applicant already60 in IST'
