"""Decrypt a snapshot and prove restoration in a NEW offline disposable PostgreSQL environment."""
import argparse,hashlib,json,os,subprocess,tarfile,tempfile,time,uuid
from pathlib import Path
from backup import run,MANIFEST_SQL
IMAGE='postgres:15@sha256:baa22e6286bf832cb147b8bb5aa9d8bc999846d69c21d11bf63f58fbeed1fa48'


def verify(archive,passphrase):
    name='sewasetu-restore-check-'+uuid.uuid4().hex[:12]
    created=False
    with tempfile.TemporaryDirectory(prefix='sewasetu-restore-check-') as temporary:
        work=Path(temporary);os.chmod(work,0o700)
        tarpath=work/'snapshot.tar'
        run(['gpg','--batch','--pinentry-mode','loopback','--passphrase-file',str(passphrase),'--output',str(tarpath),'--decrypt',str(archive)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        with tarfile.open(tarpath) as tar:
            for member in tar.getmembers():
                target=(work/member.name).resolve()
                if not target.is_relative_to(work) or not (member.isfile() or member.isdir()):raise ValueError('Unsafe snapshot member')
            tar.extractall(work,filter='data')
        expected=json.loads((work/'manifest.json').read_text())
        for file,checksum in expected['files'].items():
            target=(work/file).resolve()
            if not target.is_relative_to(work) or hashlib.sha256(target.read_bytes()).hexdigest()!=checksum:raise ValueError('Snapshot checksum mismatch')
        try:
            run(['docker','run','-d','--name',name,'--network','none','--tmpfs','/var/lib/postgresql/data:rw,size=1g','-e','POSTGRES_USER=sewasetu','-e','POSTGRES_DB=sewasetu','-e','POSTGRES_HOST_AUTH_METHOD=trust',IMAGE],stdout=subprocess.DEVNULL)
            created=True
            for _ in range(90):
                probe=subprocess.run(['docker','exec',name,'pg_isready','-U','sewasetu','-d','sewasetu'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                if probe.returncode==0:break
                time.sleep(1)
            else:raise RuntimeError('Disposable database did not become ready')
            with (work/'database.dump').open('rb') as source:
                run(['docker','exec','-i',name,'pg_restore','-U','sewasetu','-d','sewasetu','--no-owner','--no-acl','--exit-on-error'],stdin=source,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            # Same aggregate query as snapshot, against this container only.
            restored=json.loads(run(['docker','exec',name,'psql','-U','sewasetu','-d','sewasetu','-Atc',MANIFEST_SQL],capture_output=True,text=True).stdout)
            assert restored==expected['database'],'Restored rows/accounts/sequence do not reconcile'
            files=0
            for directory in ['uploads','gateway']:
                run(['docker','cp',str(work/directory),name+':/tmp/'+directory],stdout=subprocess.DEVNULL)
                for file,checksum in expected['files'].items():
                    if not file.startswith(directory+'/'):continue
                    result=run(['docker','exec',name,'sha256sum','/tmp/'+file],capture_output=True,text=True).stdout.split()[0]
                    assert result==checksum,'Restored file mismatch'
                    files+=1
            print(json.dumps({'isolated_restore':True,'network':'none','database':restored,'verified_runtime_files':files,'source_sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}))
        finally:
            if created:run(['docker','rm','-f',name],stdout=subprocess.DEVNULL)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',required=True,type=Path)
    parser.add_argument('--passphrase-file',required=True,type=Path)
    args=parser.parse_args();verify(args.archive.resolve(),args.passphrase_file.resolve())
