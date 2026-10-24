"""Cron launcher: fixed entrypoints with the private runtime environment."""
import json
import os
from pathlib import Path
import runpy
import sys
root=Path(__file__).resolve().parents[1]
entries={'statutory':root/'scripts/deemed_approval.py','outbox':root/'notifications.py','cleanup':root/'scripts/cleanup.py'}
if len(sys.argv)!=2 or sys.argv[1] not in entries:
    raise SystemExit('Unknown scheduled entrypoint')
entry=entries[sys.argv[1]]
os.environ.update(json.loads(Path('/run/sewasetu/runtime.json').read_text()))
sys.path.insert(0,str(root))
sys.argv=[str(entry)]
try:
    runpy.run_path(str(entry),run_name='__main__')
except Exception as error:
    print('Scheduled entrypoint failed: '+type(error).__name__,file=sys.stderr)
    raise SystemExit(1)
