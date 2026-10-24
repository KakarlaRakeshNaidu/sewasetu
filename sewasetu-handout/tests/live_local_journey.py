"""Bounded verification of the user's localhost Compose service. Keeps one labeled synthetic record."""
import argparse,io,json,re,subprocess,time
from pathlib import Path
from playwright.sync_api import sync_playwright
from pypdf import PdfReader
from browser_journey import apply,fit,code
ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8000'


def sql(query):
    return json.loads(subprocess.run(['docker','compose','exec','-T','db','psql','-U','sewasetu','-d','sewasetu','-Atc',query],cwd=ROOT,check=True,capture_output=True,text=True).stdout)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admin-file',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();args.output.mkdir(mode=0o700,parents=True,exist_ok=True)
    credentials=json.loads(args.admin_file.read_text())
    # One generated synthetic contact; never submit under an inherited citizen's identity.
    mobile='95'+str(time.time_ns())[-8:]
    name='Synthetic QA / श्रीमती कमला देवी शर्मा / শ্ৰীমতী জোনালী বৰা'
    with sync_playwright() as engine:
        browser=engine.chromium.launch(headless=True)
        try:
            context=browser.new_context(java_script_enabled=False,viewport={'width':390,'height':844})
            page=context.new_page();started=time.monotonic()
            ident=apply(page,context,BASE,mobile,name,widow=True,exercise_error=True,dob='04/12/1957')
            record=sql("SELECT json_build_object('name',applicant_name,'dob',dob::text,'bank',bank_account,'submitted',submitted_at,'status',status,'number',application_no) FROM applications WHERE id="+str(ident))
            assert record['name']==name and record['dob']=='1957-12-04' and record['bank']=='000012345678'
            receipt=context.request.get(BASE+f'/ack/{ident}.pdf');assert receipt.status==200
            content='\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(receipt.body())).pages)
            assert name in content and '04/12/1957' in content and '000012345678' in content
            (args.output/'live-indic-receipt.pdf').write_bytes(receipt.body())
            page.goto(BASE+f'/application/{ident}');fit(page)
            page.screenshot(path=str(args.output/'live-indic-status.png'),full_page=True)
            staff=browser.new_context();staff_page=staff.new_page();staff_page.goto(BASE+'/admin')
            staff_page.get_by_label('Staff username').fill(credentials['username']);staff_page.get_by_label('Password',exact=True).fill(credentials['password'])
            staff_page.get_by_role('button',name='Sign in',exact=True).click()
            assert 'Dashboard' in staff_page.locator('main').inner_text(),'Configured staff credentials did not work'
            assert name in staff.request.get(BASE+f'/admin/application/{ident}').text()
            anonymous=browser.new_context()
            assert anonymous.request.get(BASE+f'/ack/{ident}.pdf').status in (401,403,404)
            assert anonymous.request.get(BASE+f'/document/{ident}').status in (401,403,404)
            assert anonymous.request.get(BASE+'/__gateway/api/send').status==404
            assert context.request.get(BASE+f'/document/{ident}').status==200
            # Respect the actual shared-worker 60s send cooldown; no assessor/test bypass.
            page.get_by_role('link',name='Correct application',exact=True).click()
            page.get_by_label('Bank account number').fill('000088887777')
            page.get_by_role('button',name='Review correction',exact=True).click()
            while time.monotonic()-started<62:time.sleep(min(2,62-(time.monotonic()-started)))
            page.get_by_role('button',name='Send verification code for this correction',exact=True).click()
            page.get_by_label('Six-digit code').fill(code(context,BASE,mobile));page.get_by_role('button',name='Verify and continue').click()
            page.get_by_role('button',name='Confirm correction',exact=True).click()
            assert '000088887777' in page.locator('main').inner_text()
            corrected=sql("SELECT json_build_object('submitted',submitted_at,'status',status,'number',application_no,'bank',bank_account) FROM applications WHERE id="+str(ident))
            assert all(corrected[key]==record[key] for key in ['submitted','status','number']) and corrected['bank']=='000088887777'
            inherited=sql("SELECT json_build_object('id',id,'mobile',mobile,'name',applicant_name,'dob',to_char(dob,'DD/MM/YYYY'),'bank',bank_account) FROM applications WHERE id=1")
            legacy=browser.new_context();legacy_page=legacy.new_page();legacy_page.goto(BASE+'/status')
            legacy_page.get_by_label('Mobile number').fill(inherited['mobile']);legacy_page.get_by_role('button').filter(has_text='code').click()
            legacy_page.get_by_label('Six-digit code').fill(code(legacy,BASE,inherited['mobile']));legacy_page.get_by_role('button',name='Verify and continue').click()
            legacy_page.goto(BASE+'/application/1');html=legacy_page.locator('main').inner_text()
            assert inherited['name'] in html and inherited['dob'] in html and inherited['bank'] in html
            other=legacy.request.get(BASE+f'/application/{ident}')
            assert name not in other.text()
            report={'localhost':BASE,'synthetic_application_id':ident,'indic_db_html_pdf_exact':True,'ambiguous_dob_december':True,
                'no_js_mobile_widow_journey':True,'configured_staff_access':True,'gateway_delivered_otp':True,
                'owner_correction_preserves_sla':True,'inherited_otp_access_unchanged_values':True,'anonymous_and_nonowner_denied':True,
                'real_otp_cooldown_seconds':60,'synthetic_record_retained':True}
            (args.output/'result.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
        finally:browser.close()


if __name__=='__main__':main()
