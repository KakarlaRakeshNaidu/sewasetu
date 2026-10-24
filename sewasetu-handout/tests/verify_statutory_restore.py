"""Private full-data catch-up and separate backup restore; never accepts a target URL."""
import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
from contextlib import closing
from datetime import datetime,timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo
from werkzeug.security import generate_password_hash
from pg_runtime import PostgresSandbox
from restore_baseline import source_tables,reconcile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from manage import migrate


def query(db,statement,params=()):
    with closing(db.connect()) as conn,conn:
        with conn.cursor() as cur:
            cur.execute(statement,params)
            return cur.fetchall()


def snapshot(db):
    return dict(query(db,'SELECT id,to_jsonb(a) FROM applications a'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed',type=Path,required=True)
    parser.add_argument('--sha256',required=True)
    args=parser.parse_args()
    raw=args.seed.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==args.sha256
    source=source_tables(raw.decode('utf-8'))
    with PostgresSandbox() as db:
        db.load(ROOT/'app/schema.sql');db.load(args.seed.resolve())
        baseline=reconcile(db,source)
        with closing(db.connect()) as conn:
            migrate(conn)
        before=snapshot(db)
        accounts_before=query(db,'SELECT mobile,password_hash FROM portal_users ORDER BY mobile')
        backup=db.root/'baseline.dump'
        subprocess.run([str(db.bin/'pg_dump'),'-Fc','-h',str(db.socket),'-U','sewasetu_test','-d','postgres','-f',str(backup)],check=True,capture_output=True)
        backup.chmod(0o600)
        # Prove this archive restores into a separate fresh cluster, including migrated schema.
        with PostgresSandbox() as restored:
            subprocess.run([str(restored.bin/'pg_restore'),'-h',str(restored.socket),'-U','sewasetu_test','-d','postgres',str(backup)],check=True,capture_output=True)
            assert reconcile(restored,source)==baseline
        instant=datetime.now(ZoneInfo('Asia/Kolkata'))
        cutoff=(instant-timedelta(days=15)).replace(tzinfo=None)
        eligible={ident for ident,row in before.items() if row['status']=='PENDING' and datetime.fromisoformat(row['submitted_at'])<=cutoff}
        manifest=db.root/'catchup-manifest.json'
        manifest.write_text(json.dumps({str(i):before[i] for i in sorted(eligible)}),encoding='utf-8')
        manifest.chmod(0o600)
        env=dict(os.environ,APP_ENV='test',SECRET_KEY=secrets.token_hex(32),OTP_HMAC_KEY=secrets.token_hex(32),
            ADMIN_USERNAME='synthetic-staff',ADMIN_PASSWORD_HASH=generate_password_hash('Synthetic-Only-Staff-Password'),
            PUBLIC_BASE_URL='http://localhost',SESSION_COOKIE_SECURE='false',
            DATABASE_URL='postgresql+psycopg2://sewasetu_test@/postgres?host='+quote(str(db.socket),safe=''),
            SMS_GATEWAY_URL='http://127.0.0.1:9')
        command=[sys.executable,'-B',str(ROOT/'app/scripts/deemed_approval.py')]
        dry=subprocess.run(command+['--dry-run'],env=env,check=True,capture_output=True,text=True)
        assert int(dry.stdout.split(':')[-1])==len(eligible)
        for _ in range(2):
            subprocess.run(command,env=env,check=True,capture_output=True,text=True)
        after=snapshot(db)
        changed={i for i in before if before[i]!=after[i]}
        assert changed==eligible
        immutable=lambda row:{k:v for k,v in row.items() if k not in ('status','decided_at','decided_by')}
        assert all(immutable(before[i])==immutable(after[i]) for i in before)
        assert all(after[i]['status']=='DEEMED_APPROVED' and after[i]['decided_by']=='RTPS-AUTO' for i in eligible)
        assert query(db,'SELECT count(*) FROM application_audit')[0][0]==len(eligible)
        assert query(db,'SELECT count(*),count(DISTINCT event_key) FROM notification_outbox')[0]==(len(eligible),len(eligible))
        assert query(db,'SELECT last_value,is_called FROM applications_id_seq')[0]==(100012,True)
        manifest.write_text(json.dumps({str(i):{'before':before[i],'after':after[i]} for i in sorted(changed)}),encoding='utf-8')
        accounts=query(db,'SELECT mobile,password_hash FROM portal_users ORDER BY mobile')
        # Independent baseline records are preserved; no DOB-derived reconstruction.
        assert accounts==accounts_before and len(accounts)==100002
        print(json.dumps({'original_counts':{k:v['rows'] for k,v in baseline.items() if isinstance(v,dict) and 'rows' in v},
            'executed_at_ist':instant.isoformat(),'eligible_and_changed':len(eligible),'immutable_values_preserved':True,
            'separate_empty_backup_restore_verified':True,'backup_sha256':hashlib.sha256(backup.read_bytes()).hexdigest(),
            'private_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
            'rerun_duplicate_events':0,'sequence_preserved':True,'live_database_changed':False}))


if __name__=='__main__':
    main()
