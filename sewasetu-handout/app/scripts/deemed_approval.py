"""Nightly statutory decisions: shared config, guarded batches, audit and durable outbox."""
import argparse
import sys
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from runtime import settings,Database
from notifications import enqueue


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    config=settings()
    database=Database(config)
    conn=database.connect()
    count=0
    locked=False
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(72130982)')
            locked=cur.fetchone()[0]
        conn.commit()
        if not locked:
            print('Statutory job already running');return 0
        if args.dry_run:
            instant=datetime.now(ZoneInfo('Asia/Kolkata'))
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM applications WHERE status='PENDING' AND submitted_at<=%s",((instant-timedelta(days=config['sla_days'])).replace(tzinfo=None),))
                print('Eligible pending applications:',cur.fetchone()[0])
            conn.rollback();return 0
        while True:
            instant=datetime.now(ZoneInfo('Asia/Kolkata'))
            cutoff=(instant-timedelta(days=config['sla_days'])).replace(tzinfo=None)
            with conn.cursor() as cur:
                # A competing manual decision is rechecked after the row lock is obtained.
                cur.execute("SELECT id,application_no,mobile FROM applications WHERE status='PENDING' AND submitted_at<=%s ORDER BY submitted_at,id LIMIT 100 FOR UPDATE",(cutoff,))
                batch=cur.fetchall()
                for ident,number,mobile in batch:
                    cur.execute("UPDATE applications SET status='DEEMED_APPROVED',decided_at=%s,decided_by='RTPS-AUTO' WHERE id=%s AND status='PENDING' RETURNING id",(instant.replace(tzinfo=None),ident))
                    if cur.fetchone():
                        cur.execute("INSERT INTO application_audit(application_id,event,actor,occurred_at) VALUES(%s,'DEEMED_APPROVED','RTPS-AUTO',%s)",(ident,instant))
                        enqueue(cur,ident,'DEEMED_APPROVED',mobile,'Sewa Setu: your pension application '+number+' stands approved under the RTPS Act.',instant)
                        count+=1
            conn.commit()
            if len(batch)<100:
                break
        print('Deemed-approved applications:',count)
        return 0
    except BaseException:
        conn.rollback()
        raise
    finally:
        if locked:
            with conn.cursor() as cur:
                cur.execute('SELECT pg_advisory_unlock(72130982)')
            conn.commit()
        conn.close()
        if database.engine is not None:
            database.engine.dispose()


if __name__=='__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print('Statutory job failed: '+type(error).__name__,file=sys.stderr)
        sys.exit(1)
