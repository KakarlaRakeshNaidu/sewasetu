"""Validate private inputs before Compose can initialize a database volume."""
import hashlib,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
seed=ROOT/'seed/seed.sql'
if not seed.is_file() or hashlib.sha256(seed.read_bytes()).hexdigest()!='0738bde4c5cba055475328d740106844d6640f77ea02b1b703e629dbf231f320':raise SystemExit('Provide the unchanged original private seed/seed.sql')
if not (ROOT/'.env').is_file():raise SystemExit('Configure a private .env first')
subprocess.run(['docker','compose','config','--quiet'],cwd=ROOT,check=True)
print('Private seed hash and Compose configuration verified; no database changed')
