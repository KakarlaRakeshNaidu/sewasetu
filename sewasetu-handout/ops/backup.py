"""Quiesced encrypted Compose snapshot. Run as private local/VM operator, never web user."""
import argparse,hashlib,json,os,subprocess,tarfile,tempfile
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
COMPOSE=['docker','compose','--project-directory',str(ROOT)]


def run(args,**kw):
    return subprocess.run(args,check=True,**kw)


def dc(*args,**kw):return run(COMPOSE+list(args),**kw)


def container(service):return dc('ps','--all','-q',service,capture_output=True,text=True).stdout.strip()


MANIFEST_SQL="SELECT json_build_object('applications', (SELECT count(*) FROM applications),'accounts',(SELECT count(*) FROM portal_users),'application_digest',(SELECT md5(string_agg(md5(a::text),'' ORDER BY id)) FROM applications a),'account_digest',(SELECT md5(string_agg(md5(u::text),'' ORDER BY mobile)) FROM portal_users u),'sequence',(SELECT json_build_object('last_value',last_value,'is_called',is_called) FROM applications_id_seq))"


def manifest():
    return json.loads(dc('exec','-T','db','psql','-U','sewasetu','-d','sewasetu','-Atc',MANIFEST_SQL,capture_output=True,text=True).stdout)


def backup(destination,passphrase):
    if not passphrase.is_file() or passphrase.stat().st_mode&0o077:
        raise ValueError('Use an existing operator-owned 0600 passphrase file outside Git')
    destination.mkdir(mode=0o700,parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output=destination/('sewasetu-'+stamp+'.tar.gpg')
    if output.exists():raise ValueError('Snapshot already exists')
    running=dc('ps','--services','--status','running',capture_output=True,text=True).stdout.split()
    stopped=[name for name in ['scheduler','app','smsgw'] if name in running]
    with tempfile.TemporaryDirectory(prefix='sewasetu-backup-') as temporary:
        work=Path(temporary);os.chmod(work,0o700)
        try:
            if stopped:dc('stop',*stopped,stdout=subprocess.DEVNULL)
            before=manifest()
            with (work/'database.dump').open('wb') as file:
                dc('exec','-T','db','pg_dump','-U','sewasetu','-d','sewasetu','-Fc',stdout=file)
            for service,source,target in [('app','/var/sewasetu/uploads','uploads'),('smsgw','/var/sewasetu/gateway','gateway')]:
                run(['docker','cp',container(service)+':'+source,str(work/target)],stdout=subprocess.DEVNULL)
            after=manifest();assert before==after,'Database changed during snapshot'
            info={'created_utc':stamp,'database':before,'files':{str(f.relative_to(work)):hashlib.sha256(f.read_bytes()).hexdigest() for f in work.rglob('*') if f.is_file()}}
            (work/'manifest.json').write_text(json.dumps(info,sort_keys=True))
            archive=work/'snapshot.tar'
            with tarfile.open(archive,'w') as tar:
                for name in ['database.dump','uploads','gateway','manifest.json']:tar.add(work/name,arcname=name)
            run(['gpg','--batch','--yes','--pinentry-mode','loopback','--passphrase-file',str(passphrase),'--symmetric','--cipher-algo','AES256','--output',str(output),str(archive)],stdout=subprocess.DEVNULL)
            os.chmod(output,0o600)
        finally:
            if stopped:dc('start',*stopped,stdout=subprocess.DEVNULL)
    print(json.dumps({'backup':str(output),'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}))
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',required=True,type=Path)
    parser.add_argument('--passphrase-file',required=True,type=Path)
    args=parser.parse_args();backup(args.destination.resolve(),args.passphrase_file.resolve())
