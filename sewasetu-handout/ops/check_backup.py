"""Backup freshness check for a host timer/external operator alert."""
import argparse,json,time
from pathlib import Path
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--directory',required=True,type=Path)
parser.add_argument('--max-age-hours',type=float,default=30)
args=parser.parse_args()
files=list(args.directory.glob('sewasetu-*.tar.gpg'))
latest=max(files,key=lambda p:p.stat().st_mtime) if files else None
age=(time.time()-latest.stat().st_mtime)/3600 if latest else None
ok=bool(latest and latest.stat().st_size>0 and age<=args.max_age_hours)
print(json.dumps({'backup_fresh':ok,'age_hours':round(age,2) if age is not None else None}))
raise SystemExit(0 if ok else 1)
