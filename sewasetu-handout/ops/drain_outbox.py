"""Bounded operator catch-up using the normal worker, retries and actual gateway confirmation."""
import argparse,json,time
from backup import dc
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--max-seconds',type=int,default=300)
parser.add_argument('--max-batches',type=int,default=200)
args=parser.parse_args()
if not 1<=args.max_seconds<=900 or not 1<=args.max_batches<=300:raise SystemExit('Use bounded catch-up: seconds1..900, batches1..300')
started=time.monotonic();batches=0

def state():
    value=dc('exec','-T','db','psql','-U','sewasetu','-d','sewasetu','-Atc',"SELECT COALESCE(json_object_agg(status,n),'{}'::json) FROM (SELECT status,count(*) n FROM notification_outbox GROUP BY status) x",capture_output=True,text=True).stdout
    return json.loads(value)

while batches<args.max_batches and time.monotonic()-started<args.max_seconds:
    counts=state()
    if not counts.get('PENDING',0) and not counts.get('QUEUED',0):break
    result=dc('exec','-T','app','python','-c',"from runtime import settings,Database;from notifications import run_worker;print('Outbox entries checked:',run_worker(Database(settings()),limit=100))",capture_output=True,text=True)
    batches+=1
    if batches%20==0:print(json.dumps({'batches':batches,'status':state()}),flush=True)
    if result.stdout.strip().endswith(': 0'):time.sleep(2)
counts=state()
complete=not any(counts.get(k,0) for k in ['PENDING','QUEUED','FAILED'])
print(json.dumps({'batches':batches,'elapsed_seconds':round(time.monotonic()-started,2),'status':counts,'all_delivery_confirmed':complete}))
raise SystemExit(0 if complete else 1)
