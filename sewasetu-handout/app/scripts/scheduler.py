"""Start cron with a private allowlisted environment, never owner credentials."""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from runtime import settings
settings()
allowed={'APP_ENV','PUBLIC_BASE_URL','SECRET_KEY','OTP_HMAC_KEY','DATABASE_URL','ADMIN_USERNAME','ADMIN_PASSWORD_HASH',
'SMS_GATEWAY_URL','UPLOAD_DIR','SESSION_COOKIE_SECURE','SCHEME_DEADLINE','APP_TIMEZONE','MIN_AGE','SLA_DAYS','DB_POOL_SIZE','DB_POOL_OVERFLOW'}
path=Path('/run/sewasetu')
path.mkdir(mode=0o700,parents=True,exist_ok=True)
fd=os.open(path/'runtime.json',os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as file:
    json.dump({key:value for key,value in os.environ.items() if key in allowed},file)
os.execvp('cron',['cron','-f'])
