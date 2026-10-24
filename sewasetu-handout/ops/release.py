"""Build and start a clean committed local release; performs no Git mutations."""
import json,os,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def run(args,**kw):return subprocess.run(args,cwd=ROOT,check=True,**kw)


def main():
    if not (ROOT/'docker-compose.override.yml').is_file():raise SystemExit('Localhost override is required; use documented VM deployment steps for production')
    dirty=run(['git','status','--porcelain'],capture_output=True,text=True).stdout
    if dirty:raise SystemExit('Commit/review working-tree changes before release')
    sha=run(['git','rev-parse','HEAD'],capture_output=True,text=True).stdout.strip()
    env=dict(os.environ,RELEASE_SHA=sha)
    from backup import backup
    backup(ROOT/'backups',ROOT/'secrets/backup-passphrase')
    command=['docker','compose']
    run(command+['build','app','smsgw'],env=env)
    if run(['git','status','--porcelain'],capture_output=True,text=True).stdout or run(['git','rev-parse','HEAD'],capture_output=True,text=True).stdout.strip()!=sha:
        raise SystemExit('Checkout changed during build; review and rebuild')
    # Verify baked files against committed blobs, not only an image label.
    import hashlib
    for service,context in [('app','app'),('gateway','smsgw')]:
        files=run(['git','ls-files','-z','--',context],capture_output=True).stdout.decode().split('\0')
        expected={}
        for relative in files:
            if not relative:continue
            inner=relative[len(context)+1:]
            included=(inner.endswith('.py') and '/' not in inner) or inner in ('requirements.txt','schema.sql') or inner.startswith(('migrations/','scripts/','deploy/','templates/','static/'))
            if included:
                repository_path='sewasetu-handout/'+relative
                blob=run(['git','show',sha+':'+repository_path],capture_output=True).stdout
                expected[inner]=hashlib.sha256(blob).hexdigest()
        assert expected,'No committed production files discovered'
        probe="import sys,json,hashlib;from pathlib import Path;m=json.load(sys.stdin);bad=[p for p,h in m.items() if not (Path('/app')/p).is_file() or hashlib.sha256((Path('/app')/p).read_bytes()).hexdigest()!=h];print(json.dumps(bad));sys.exit(bool(bad))"
        result=run(['docker','run','--rm','-i','--network','none','--entrypoint','python','sewasetu-'+service+':'+sha,'-c',probe],input=json.dumps(expected),text=True,capture_output=True)
        assert json.loads(result.stdout)==[],'Baked source differs from committed release'

    run(command+['up','-d','db'],env=env)
    run(command+['stop','scheduler'],env=env)
    run(command+['--profile','operator','run','--rm','migrate'],env=env)
    run(command+['--profile','scheduler','up','-d','--wait','--wait-timeout','150'],env=env)
    for _ in range(30):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=5) as response:info=json.load(response)
            assert info['release']==sha and info['status']=='ready'
            break
        except Exception:time.sleep(1)
    else:raise SystemExit('Release did not become ready')
    # Persist only the public release identifier; do not change any secret value.
    path=ROOT/'.env';lines=path.read_text().splitlines()
    lines=[line for line in lines if not line.startswith('RELEASE_SHA=')]
    path.write_text('\n'.join(lines+['RELEASE_SHA='+sha])+'\n');os.chmod(path,0o600)
    (ROOT/'docs').mkdir(exist_ok=True)
    (ROOT/'docs/local-release.json').write_text(json.dumps(info,indent=2)+'\n')
    print(json.dumps(info))


if __name__=='__main__':main()
