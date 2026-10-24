"""Local clean-clone setup with original private seed/env; owns only an isolated temporary Compose project."""
import json,os,secrets,shutil,subprocess,tempfile
from pathlib import Path
from backup import MANIFEST_SQL
APP=Path(__file__).resolve().parents[1]


def run(args,**kw):return subprocess.run(args,check=True,capture_output=True,**kw)


def main(admin_file):
    if run(['git','status','--porcelain'],cwd=APP).stdout:raise SystemExit('Review/commit changes before clean-clone verification')
    root=run(['git','rev-parse','--show-toplevel'],cwd=APP,text=True).stdout.strip()
    sha=run(['git','rev-parse','HEAD'],cwd=APP,text=True).stdout.strip()
    project='sewasetu-clone-check-'+secrets.token_hex(5)
    with tempfile.TemporaryDirectory(prefix='sewasetu-clone-check-') as temporary:
        work=Path(temporary);os.chmod(work,0o700)
        checkout=work/'repository'
        run(['git','clone','--no-hardlinks','--quiet',root,str(checkout)])
        cloned=checkout/'sewasetu-handout'
        (cloned/'seed').mkdir(exist_ok=True)
        shutil.copyfile(APP/'seed/seed.sql',cloned/'seed/seed.sql');os.chmod(cloned/'seed/seed.sql',0o644)  # DB uid reads this bind; private ancestor directory is0700
        shutil.copyfile(APP/'.env',cloned/'.env');os.chmod(cloned/'.env',0o600)
        override=work/'private-compose.yml';override.write_text('services:\n  app:\n    ports: !override []\n')
        env=dict(os.environ,RELEASE_SHA=sha)
        command=['docker','compose','--project-directory',str(cloned),'-p',project,'-f',str(cloned/'docker-compose.yml'),'-f',str(cloned/'docker-compose.override.yml'),'-f',str(override)]
        def dc(*args,**kw):return run(command+list(args),env=env,**kw)
        try:
            dc('config','--quiet')
            dc('build','app','smsgw')
            dc('up','-d','--wait','--wait-timeout','120','db')
            dc('--profile','operator','run','--rm','migrate')
            dc('--profile','operator','run','--rm','migrate','python','/app/manage.py','rotate-roles')
            dc('up','-d','--wait','--wait-timeout','150','app','smsgw','init')
            baseline=json.loads(dc('exec','-T','db','psql','-U','sewasetu','-d','sewasetu','-Atc',MANIFEST_SQL,text=True).stdout)
            assert baseline=={'applications':100002,'accounts':100002,'application_digest':'5ef7ecf402224ab4cca12ea22e856da8','account_digest':'090a4e43dd6f25a31f620a60f2ec029a','sequence':{'last_value':100012,'is_called':True}},'Clean clone seed values do not reconcile'
            probe="import json,urllib.request;print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=5))))"
            health=json.loads(dc('exec','-T','app','python','-c',probe,text=True).stdout)
            assert health=={'status':'ready','release':sha}
            # Input is private JSON on stdin, never credentials in command arguments or logs.
            login='import sys,json,re,requests;c=json.load(sys.stdin);s=requests.Session();base=\'http://127.0.0.1:8000\';page=s.get(base+\'/admin\',timeout=5);token=re.search(r\'name="csrf_token" value="([^"]+)"\',page.text)[1];r=s.post(base+\'/admin\',data={\'csrf_token\':token,\'username\':c[\'username\'],\'password\':c[\'password\']},timeout=5,allow_redirects=False);assert r.status_code in (302,303);assert s.get(base+\'/admin/dashboard\',timeout=5).status_code==200;assert s.get(base+\'/__gateway/\',timeout=5).status_code==200;print(\'private staff and gateway verified\')'
            dc('exec','-T','app','python','-c',login,input=admin_file.read_bytes())
            assert run(['git','status','--porcelain'],cwd=cloned).stdout==b''
            print(json.dumps({'clean_local_clone':True,'release':sha,'private_original_seed_and_env':True,'restored':baseline,'healthy':True,'staff_gateway':True,'published_ports':[],'scheduler_disabled_for_baseline':True}))
        finally:
            # Never compose down -v. Inspect and remove only this uniquely owned disposable project.
            ids=dc('ps','--all','-q',text=True).stdout.split()
            volumes=set()
            for identifier in ids:
                info=json.loads(run(['docker','inspect',identifier],text=True).stdout)[0]
                assert info['Config']['Labels'].get('com.docker.compose.project')==project
                for mount in info.get('Mounts',[]):
                    if mount['Type']=='volume' and mount['Name'].startswith(project+'_'):volumes.add(mount['Name'])
                run(['docker','rm','-f',identifier])
            for volume in volumes:
                info=json.loads(run(['docker','volume','inspect',volume],text=True).stdout)[0]
                assert info['Labels'].get('com.docker.compose.project')==project
                run(['docker','volume','rm',volume])
            networks=run(['docker','network','ls','--filter','label=com.docker.compose.project='+project,'-q'],text=True).stdout.split()
            for network in networks:run(['docker','network','rm',network])


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--admin-file',required=True,type=Path)
    args=parser.parse_args();main(args.admin_file.resolve())
