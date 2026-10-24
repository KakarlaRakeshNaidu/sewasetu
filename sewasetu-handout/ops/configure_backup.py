"""Generate a NEW private backup key; never overwrite an existing recovery key."""
import os,secrets
from pathlib import Path
path=Path(__file__).resolve().parents[1]/'secrets/backup-passphrase'
path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as file:file.write(secrets.token_urlsafe(48))
print('Private backup key created. Keep a separate secure recovery copy; no key printed.')
