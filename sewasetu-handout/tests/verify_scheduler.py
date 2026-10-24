"""Real cron/entrypoint at 02:00 IST in an isolated synthetic Docker network; clock shim is test-only."""
import json,os,secrets,subprocess,tempfile,time,uuid
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from werkzeug.security import generate_password_hash
ROOT=Path(__file__).resolve().parents[1]
PG='postgres:15@sha256:baa22e6286bf832cb147b8bb5aa9d8bc999846d69c21d11bf63f58fbeed1fa48'


def run(args,**kw):return subprocess.run(['docker',*args],check=True,capture_output=True,**kw)


def main():
    suffix=uuid.uuid4().hex[:10];network='sewasetu-cron-check-'+suffix
    db=network+'-db';gateway=network+'-gateway';cron=network+'-cron';created=[]
    run(['network','create','--internal',network])
    with tempfile.TemporaryDirectory(prefix='sewasetu-cron-') as temporary:
        work=Path(temporary);os.chmod(work,0o700)
        try:
            run(['run','-d','--name',db,'--network',network,'--network-alias','db','--tmpfs','/var/lib/postgresql/data:rw,size=1g',
                '-e','POSTGRES_USER=sewasetu','-e','POSTGRES_DB=sewasetu','-e','POSTGRES_HOST_AUTH_METHOD=trust',PG]);created.append(db)
            for _ in range(60):
                probe=subprocess.run(['docker','exec',db,'pg_isready','-U','sewasetu','-d','sewasetu'],capture_output=True)
                if probe.returncode==0:break
                time.sleep(1)
            else:raise RuntimeError('Synthetic database startup failed')
            def sql(statement):return run(['exec','-i',db,'psql','-U','sewasetu','-d','sewasetu','-At','-v','ON_ERROR_STOP=1'],input=statement.encode()).stdout.decode().strip()
            sql((ROOT/'app/schema.sql').read_text())
            password=secrets.token_urlsafe(36)
            owner=work/'operator.env';owner.write_text('MIGRATION_DATABASE_URL=postgresql://sewasetu:'+password+'@db:5432/sewasetu\nMIGRATION_DATABASE_PASSWORD='+password+'\nRUNTIME_DATABASE_PASSWORD='+password+'-runtime\n');os.chmod(owner,0o600)
            for command in ['migrate','rotate-roles']:
                run(['run','--rm','--network',network,'--env-file',str(owner),'sewasetu-app:local','python','/app/manage.py',command])
            now=datetime.now(ZoneInfo('Asia/Kolkata'));target=now.replace(hour=1,minute=59,second=50,microsecond=0)
            if target<=now:target+=timedelta(days=1)
            old=(target-timedelta(days=20)).replace(tzinfo=None).isoformat();recent=(target-timedelta(days=1)).replace(tzinfo=None).isoformat()
            sql("INSERT INTO applications(application_no,applicant_name,mobile,dob,status,submitted_at,decided_by) VALUES ('SYNTH-CRON-DUE','Synthetic Cron Due','9410000000','1950-01-01','PENDING','"+old+"',NULL),('SYNTH-CRON-RECENT','Synthetic Cron Recent','9410000001','1950-01-01','PENDING','"+recent+"',NULL),('SYNTH-CRON-MANUAL','Synthetic Cron Manual','9410000002','1950-01-01','APPROVED','"+old+"','synthetic-staff')")
            run(['run','-d','--name',gateway,'--network',network,'--network-alias','smsgw','sewasetu-gateway:local']);created.append(gateway)
            offset=int((target-datetime.now(ZoneInfo('Asia/Kolkata'))).total_seconds())
            values={'APP_ENV':'production','PUBLIC_BASE_URL':'https://synthetic.invalid','SECRET_KEY':secrets.token_urlsafe(48),'OTP_HMAC_KEY':secrets.token_urlsafe(48),
                'ADMIN_USERNAME':'synthetic-staff','ADMIN_PASSWORD_HASH':generate_password_hash('Synthetic-Only-Cron-Staff-Password'),
                'DATABASE_URL':'postgresql+psycopg2://portal_runtime:'+password+'-runtime@db:5432/sewasetu','SESSION_COOKIE_SECURE':'true',
                'SMS_GATEWAY_URL':'http://smsgw:8025','UPLOAD_DIR':'/tmp/cronuploads','LD_PRELOAD':'/usr/lib/x86_64-linux-gnu/faketime/libfaketime.so.1',
                'FAKETIME':('+' if offset>=0 else '')+str(offset)+'s','FAKETIME_DONT_FAKE_MONOTONIC':'1'}
            env=work/'runtime.env';env.write_text(''.join(k+'='+v+'\n' for k,v in values.items()));os.chmod(env,0o600)
            run(['run','-d','--name',cron,'--user','0:0','--network',network,'--env-file',str(env),'sewasetu-scheduler-verify:local','sleep','180']);created.append(cron)
            source=run(['exec',cron,'cat','/app/deploy/crontab']).stdout.decode()
            assert '0 2 * * * /usr/local/bin/python /app/scripts/run_job.py statutory' in source
            # The schedule/commands are unchanged; propagate the isolated clock shim to exec children.
            prelude=''.join(k+'='+values[k]+'\n' for k in ['LD_PRELOAD','FAKETIME','FAKETIME_DONT_FAKE_MONOTONIC'])
            run(['exec','-i',cron,'crontab','-'],input=(prelude+source).encode())
            zone=run(['exec',cron,'readlink','/etc/localtime']).stdout.decode().strip();assert zone.endswith('Asia/Kolkata')
            # Replace only the test container process; scheduler writes its actual allowlisted environment.
            run(['exec','-d',cron,'python','/app/scripts/scheduler.py'])
            for _ in range(85):
                result=sql("SELECT status FROM applications WHERE application_no='SYNTH-CRON-DUE'")
                if result=='DEEMED_APPROVED':break
                time.sleep(1)
            else:raise AssertionError('Actual cron did not process the overdue synthetic case at 02:00 IST')
            assert sql("SELECT extract(hour from decided_at)::int FROM applications WHERE application_no='SYNTH-CRON-DUE'")=='2'
            assert sql("SELECT status FROM applications WHERE application_no='SYNTH-CRON-RECENT'")=='PENDING'
            assert sql("SELECT status||':'||decided_by FROM applications WHERE application_no='SYNTH-CRON-MANUAL'")=='APPROVED:synthetic-staff'
            assert sql('SELECT count(*) FROM application_audit')=='1'
            assert sql('SELECT count(*) FROM notification_outbox')=='1'
            for entry in ['statutory','outbox']:
                run(['exec',cron,'python','/app/scripts/run_job.py',entry])
            for _ in range(8):
                time.sleep(3)
                run(['exec',cron,'python','/app/scripts/run_job.py','outbox'])
                if sql("SELECT status FROM notification_outbox")=='DELIVERED':break
            else:
                print('Outbox diagnostic',sql("SELECT json_build_object('status',status,'attempts',attempts,'checks',delivery_checks,'next',next_attempt_at,'error',last_error) FROM notification_outbox"))
                raise AssertionError('Synthetic outbox delivery did not confirm within bounded verification')
            assert sql('SELECT count(*) FROM application_audit')=='1'
            print(json.dumps({'actual_cron_daemon':True,'schedule':'0 2 * * *','daemon_timezone':zone,'due_processed_once':True,
                'recent_and_manual_unchanged':True,'outbox_gateway_confirmed_delivered':True,'network':'internal, no published ports',
                'clock':'isolated libfaketime offset; host and running portal clocks unchanged'}))
        finally:
            for name in reversed(created):subprocess.run(['docker','rm','-f',name],capture_output=True)
            subprocess.run(['docker','network','rm',network],capture_output=True)


if __name__=='__main__':main()
