"""Create a NEW private local environment. Never overwrite an existing deployment."""
import getpass,os,secrets,subprocess
from pathlib import Path
from werkzeug.security import generate_password_hash
ROOT=Path(__file__).resolve().parents[1]
path=ROOT/'.env'
if path.exists():raise SystemExit('Existing .env retained; configure only a fresh checkout')
admin=input('New staff username (not admin): ').strip()
if not admin or admin=='admin':raise SystemExit('Choose a non-default username')
password=getpass.getpass('New staff password (at least 16 characters): ')
if len(password)<16 or password!=getpass.getpass('Confirm staff password: '):raise SystemExit('Use matching strong passwords')
owner,runtime=secrets.token_urlsafe(36),secrets.token_urlsafe(36)
sha=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,check=True,capture_output=True,text=True).stdout.strip()
values={'APP_ENV':'production','PUBLIC_BASE_URL':'https://YOUR_OWN_HOST','SECRET_KEY':secrets.token_urlsafe(48),
'OTP_HMAC_KEY':secrets.token_urlsafe(48),'ADMIN_USERNAME':admin,'ADMIN_PASSWORD_HASH':generate_password_hash(password),
'DATABASE_URL':'postgresql+psycopg2://portal_runtime:'+runtime+'@db:5432/sewasetu',
'MIGRATION_DATABASE_URL':'postgresql://sewasetu:'+owner+'@db:5432/sewasetu','MIGRATION_DATABASE_PASSWORD':owner,
'RUNTIME_DATABASE_PASSWORD':runtime,'SCHEME_DEADLINE':'2026-11-01T00:00:00+05:30','RELEASE_SHA':sha}
# Single quoted Compose dotenv values preserve hash dollar signs literally.
if any("'" in value or '\n' in value or '\r' in value for value in values.values()):raise SystemExit('Unsupported configuration character')
fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as file:
    for key,value in values.items():file.write(key+"='"+value+"'\n")
print('Private .env created. Keep the staff password and this file outside Git; no credentials printed.')
