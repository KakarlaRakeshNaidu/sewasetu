"""Real OTP/form helpers and synthetic documents for citizen regressions."""
import io
import random
from datetime import datetime,date
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import pytest
from PIL import Image
from fpdf import FPDF
from security_support import application_challenge,query
from submission_support import prepare,otp,post_form,form_fields,Inputs,text,rows


@pytest.fixture
def citizen(portal,gateway,database,monkeypatch):
    gateway.DELAY_MIN=gateway.DELAY_MAX=0
    monkeypatch.setenv('UPLOAD_DIR',portal.UPLOAD_DIR)
    clock=SimpleNamespace(instant=datetime(2026,10,24,12,tzinfo=ZoneInfo('Asia/Kolkata')),server_zone=ZoneInfo('Asia/Kolkata'))
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return clock.instant.astimezone(tz) if tz else clock.instant.astimezone(clock.server_zone).replace(tzinfo=None)
    class Day(date):
        @classmethod
        def today(cls):return clock.instant.astimezone(clock.server_zone).date()
    monkeypatch.setattr(portal,'datetime',Clock)
    monkeypatch.setattr(portal,'date',Day)
    return SimpleNamespace(portal=portal,gateway=gateway,database=database,clock=clock,mobile='9300000000')


def verified(ctx,mobile=None):
    client=ctx.portal.app.test_client()
    mobile=mobile or ctx.mobile
    assert application_challenge(ctx,client,mobile=mobile).status_code in (302,303)
    assert post_form(client,'/verify',{'otp':otp(client,mobile)}).status_code in (302,303)
    assert client.get('/form/1').status_code==200
    return client


def pdf_bytes(size=None):
    pdf=FPDF();pdf.add_page();pdf.set_font('Helvetica',size=10);pdf.cell(50,10,'Synthetic age proof')
    if size is None:return bytes(pdf.output())
    padding=max(0,size-1100)
    for _ in range(5):
        document=FPDF();document.add_page();document.set_font('Helvetica',size=10)
        document.cell(50,10,'Synthetic age proof');document.set_title('x'*padding)
        raw=bytes(document.output())
        if len(raw)==size:return raw
        padding+=size-len(raw)
    raise AssertionError('Could not construct exact-size valid PDF fixture')


def jpg_bytes():
    output=io.BytesIO()
    Image.frombytes('RGB',(512,512),random.Random(73).randbytes(512*512*3)).save(output,format='JPEG',quality=95)
    return output.getvalue()


def upload(client,data,filename,mime):
    return client.post('/upload',data=form_fields(client.get('/upload'))|{'document':(io.BytesIO(data),filename,mime)})


def submit(client,data=None):
    fields=form_fields(client.get('/declaration'))|{'declaration':'yes'}
    return client.post('/declaration',data=fields|(data or {}),follow_redirects=True)


def existing(ctx):
    client=ctx.portal.app.test_client()
    prepare(client,ctx.mobile)
    assert submit(client).status_code==200
    record=query(ctx,'SELECT id,to_jsonb(a) FROM applications a WHERE mobile=%s',(ctx.mobile,))[0]
    return client,record[0],record[1]


def correction_review(ctx,client,app_id,changes):
    page=client.get(f'/application/{app_id}/edit')
    assert page.status_code==200,'Owner cannot open a pending correction'
    fields=form_fields(page)|changes
    response=client.post(f'/application/{app_id}/edit',data=fields,follow_redirects=True)
    assert response.status_code==200 and b'Review correction' in response.data,'Correction did not reach explicit review'
    return response


def prove_correction(ctx,client,app_id,review):
    response=post_form(client,f'/application/{app_id}/edit/request-code',page=f'/application/{app_id}/edit/review')
    assert response.status_code in (302,303),'Sensitive correction has no step-up challenge'
    response=post_form(client,f'/application/{app_id}/edit/verify',{'otp':otp(client,ctx.mobile)})
    assert response.status_code in (302,303)
    return client.get(f'/application/{app_id}/edit/review')


def confirm(client,app_id,review):
    fields=Inputs(text(review)).fields
    return client.post(f'/application/{app_id}/edit/confirm',data={k:v for k,v in fields.items() if k in ('csrf_token','version','review_digest')})
