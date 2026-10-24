"""Real Chromium checks on an owned synthetic server; accepts no external target URL."""
import argparse
import json
from pathlib import Path
import re
import time
from playwright.sync_api import sync_playwright
from http_runtime import HttpRuntime
from citizen_support import pdf_bytes,jpg_bytes


def code(context,base,mobile):
    for _ in range(80):
        response=context.request.get(base+'/__gateway/api/messages',params={'to':mobile})
        assert response.status==200
        messages=response.json()['messages']
        if messages and messages[-1]['delivered']:
            value=re.search(r'\b[0-9]{6}\b',messages[-1]['text'])
            if value:return value[0]
        time.sleep(.05)
    raise AssertionError('Actual gateway code unavailable')


def fit(page):
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth+1'),'Page overflows horizontally'


def apply(page,context,base,mobile,name,*,widow=False,jpeg=False,exercise_error=False,dob='13/12/1950'):
    page.goto(base+'/apply')
    question=page.locator('label[for=captcha]').inner_text();a,b=re.search(r'([0-9]+)\s*\+\s*([0-9]+)',question).groups()
    page.get_by_label('Mobile number').fill(mobile);page.locator('#captcha').fill(str(int(a)+int(b)))
    page.get_by_role('button',name='Send code and continue',exact=True).click()
    page.get_by_label('Six-digit code').fill(code(context,base,mobile));page.get_by_role('button',name='Verify and continue').click()
    page.get_by_label('Full name as on age proof').fill(name)
    page.get_by_label('Date of birth').fill('31/02/1950' if exercise_error else dob)
    page.get_by_label('Gender',exact=False).select_option('Female' if widow else 'Male')
    page.get_by_label('Marital status').select_option('Widowed' if widow else 'Unmarried')
    page.get_by_role('button',name='Save and continue',exact=True).click()
    if exercise_error:
        assert page.locator('.error-summary').is_visible()
        assert page.get_by_label('Full name as on age proof').input_value()==name
        page.locator('.error-summary a[href="#dob"]').click()
        assert page.locator('#dob').evaluate('(element)=>element===document.activeElement')
        page.get_by_label('Date of birth').fill(dob);page.get_by_role('button',name='Save and continue',exact=True).click()
    page.get_by_label('Village or town').fill('Synthetic Village');page.get_by_label('Block',exact=False).select_option('Sonari')
    page.get_by_role('button',name='Save and continue',exact=True).click()
    page.get_by_label('Bank account number').fill('000012345678');page.get_by_label('IFSC',exact=False).fill('TEST0000001')
    page.get_by_role('button',name='Save and continue',exact=True).click()
    raw=jpg_bytes() if jpeg else pdf_bytes(150*1024)
    page.locator('#document').set_input_files({'name':'proof.jpg' if jpeg else 'proof.pdf','mimeType':'image/jpeg' if jpeg else'application/pdf','buffer':raw})
    page.get_by_role('button',name='Upload and continue',exact=True).click()
    assert name in page.locator('main').inner_text()
    page.locator('#declaration').check();page.get_by_role('button',name='Submit application',exact=True).click()
    assert 'received' in page.locator('main').inner_text().lower()
    number=page.locator('main').inner_text();assert re.search(r'SSP[0-9]+',number)
    fit(page)
    path=page.locator('a[href^="/ack/"]').get_attribute('href')
    ident=int(re.search(r'/ack/([0-9]+)',path)[1])
    return ident


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--screenshots',type=Path)
    args=parser.parse_args()
    if args.screenshots:args.screenshots.mkdir(parents=True,exist_ok=True)
    with HttpRuntime() as server,sync_playwright() as engine:
        browser=engine.chromium.launch(headless=True)
        try:
            context=browser.new_context(java_script_enabled=False,viewport={'width':390,'height':844})
            page=context.new_page();page.goto(server.url+'/')
            page.keyboard.press('Tab');assert page.locator('.skip').evaluate('(e)=>e===document.activeElement')
            page.keyboard.press('Enter');fit(page)
            if args.screenshots:page.screenshot(path=str(args.screenshots/'home-mobile.png'),full_page=True)
            ident=apply(page,context,server.url,'9500000000','Synthetic UI Widow',widow=True,exercise_error=True)
            page.goto(server.url+f'/application/{ident}')
            page.get_by_role('link',name='Correct application',exact=True).click()
            page.get_by_label('Bank account number').fill('000088887777')
            page.get_by_role('button',name='Review correction',exact=True).click()
            time.sleep(1.1)
            page.get_by_role('button',name='Send verification code for this correction',exact=True).click()
            page.get_by_label('Six-digit code').fill(code(context,server.url,'9500000000'));page.get_by_role('button',name='Verify and continue').click()
            page.get_by_role('button',name='Confirm correction',exact=True).click()
            assert '000088887777' in page.locator('main').inner_text();fit(page)
            if args.screenshots:page.screenshot(path=str(args.screenshots/'status-corrected-mobile.png'),full_page=True)
            # No JS, distinct adults, same actual contact. Private fixture cooldown1s; production default remains60s.
            time.sleep(1.1)
            second=browser.new_context(java_script_enabled=False,viewport={'width':390,'height':844})
            page2=second.new_page();second_id=apply(page2,second,server.url,'9500000000','Synthetic UI Adult',jpeg=True)
            assert second_id!=ident
            page2.goto(server.url+'/status');assert 'Synthetic UI Widow' in page2.locator('main').inner_text() and 'Synthetic UI Adult' in page2.locator('main').inner_text()
            #200% equivalent reflow:640CSS pixels at2x device scale, matching1280-device-pixel desktop zoom.
            zoom=browser.new_context(viewport={'width':640,'height':720},device_scale_factor=2)
            zoom_page=zoom.new_page();zoom_page.goto(server.url+'/');fit(zoom_page)
            if args.screenshots:zoom_page.screenshot(path=str(args.screenshots/'home-200-percent-reflow.png'),full_page=True)
            staff=browser.new_context(viewport={'width':390,'height':844});staff_page=staff.new_page();staff_page.goto(server.url+'/admin')
            staff_page.get_by_label('Staff username').fill('synthetic-staff');staff_page.get_by_label('Password',exact=True).fill(server.staff_password)
            staff_page.get_by_role('button',name='Sign in',exact=True).click();assert 'Dashboard' in staff_page.locator('main').inner_text();fit(staff_page)
            assert staff.request.get(server.url+'/__gateway/').status==200
            assert context.request.get(server.url+f'/document/{ident}').status==200
            anonymous=browser.new_context();assert anonymous.request.get(server.url+f'/ack/{ident}.pdf').status in (401,403,404)
            print(json.dumps({'no_js_journeys':2,'widow_without_employer':True,'shared_contact_adults':2,'linked_error_input_retention':True,
                'sensitive_pending_correction':True,'keyboard_skip_and_error_focus':True,'mobile_width':390,'zoom_equivalent':200,
                'horizontal_overflow':False,'staff_gateway_preserved':True,'synthetic_only':True,'private_fixture_otp_cooldown_seconds':1}))
        finally:browser.close()


if __name__=='__main__':main()
