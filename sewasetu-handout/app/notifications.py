"""Durable outbox delivery with a bounded claim lease and explicit carrier confirmation."""
import os
import time
from contextlib import contextmanager
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import requests
from runtime import settings,Database


def enqueue(cur,app_id,event,mobile,body,instant):
    cur.execute("INSERT INTO notification_outbox(event_key,application_id,mobile,body,created_at,next_attempt_at) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(event_key) DO NOTHING",
        (event+':'+str(app_id),app_id,mobile,body,instant,instant))


def run_worker(database,instant=None,limit=50,gateway=None):
    fixed_instant=instant
    started=time.monotonic()
    gateway = (gateway or os.environ.get('SMS_GATEWAY_URL','http://smsgw:8025')).rstrip('/')
    done = 0
    @contextmanager
    def transaction():
        conn = database.connect()
        try:
            with conn.cursor() as cur:
                yield cur
            conn.commit()
        except BaseException:
            conn.rollback();raise
        finally:
            conn.close()
    for _ in range(min(100,max(1,limit))):
        if time.monotonic()-started>=40:break
        instant=fixed_instant or datetime.now(ZoneInfo('Asia/Kolkata'))
        lease=instant+timedelta(seconds=60)
        with transaction() as cur:
            cur.execute("SELECT id,event_key,mobile,body,status,attempts,gateway_id,delivery_checks FROM notification_outbox WHERE status IN ('PENDING','QUEUED') AND next_attempt_at<=%s AND (lease_until IS NULL OR lease_until<=%s) ORDER BY next_attempt_at,id LIMIT 1 FOR UPDATE SKIP LOCKED",(instant,instant))
            row=cur.fetchone()
            if not row:
                break
            cur.execute('UPDATE notification_outbox SET lease_until=%s WHERE id=%s',(lease,row[0]))
        ident,event,mobile,body,status,attempts,gateway_id,checks=row
        error=None
        delivered=False
        try:
            if status == 'PENDING':
                attempts+=1
                response=requests.post(gateway+'/api/send',json={'to':mobile,'text':body,'event_key':event},timeout=(2,5),allow_redirects=False)
                if response.status_code not in (200,202):
                    raise ValueError('HTTP_'+str(response.status_code))
                data=response.json()
                if data.get('status') not in ('queued','delivered') or not isinstance(data.get('id'),int):
                    raise ValueError('INVALID_RESPONSE')
                gateway_id=data['id'];status='QUEUED'
            else:
                checks+=1
                response=requests.get(gateway+'/api/messages',params={'to':mobile},timeout=(2,5),allow_redirects=False)
                if response.status_code != 200:
                    raise ValueError('HTTP_'+str(response.status_code))
                delivered=any(x.get('id')==gateway_id and x.get('delivered') is True for x in response.json()['messages'])
                if delivered:
                    status='DELIVERED'
        except requests.RequestException:
            error='TRANSPORT'
        except (ValueError,KeyError,TypeError) as exc:
            error=str(exc) if str(exc).startswith('HTTP_') else 'INVALID_RESPONSE'
        if (error and attempts+checks>=8) or (not delivered and checks>=8):
            status='FAILED'
        delay=min(3600,5*2**min(attempts+checks,9)) if error else 5
        completed=fixed_instant or datetime.now(ZoneInfo('Asia/Kolkata'))
        with transaction() as cur:
            cur.execute('UPDATE notification_outbox SET status=%s,attempts=%s,gateway_id=%s,delivery_checks=%s,next_attempt_at=%s,lease_until=NULL,last_error=%s,delivered_at=%s WHERE id=%s AND lease_until=%s',
                (status,attempts,gateway_id,checks,completed+timedelta(seconds=delay),error,completed if delivered else None,ident,lease))
        done+=1
    return done


if __name__=='__main__':
    try:
        print('Outbox entries checked:',run_worker(Database(settings())))
    except Exception as error:
        print('Outbox worker failed: '+type(error).__name__)
        raise SystemExit(1)
