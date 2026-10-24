"""ZIP tracked source and full audited .git outside the repository; no Git mutations."""
import argparse,hashlib,json,re,stat,subprocess,tempfile,zipfile
from pathlib import Path
APP=Path(__file__).resolve().parents[1]


def git(*args,cwd=APP):return subprocess.run(['git',*args],cwd=cwd,check=True,capture_output=True).stdout


def package(output):
    root=Path(git('rev-parse','--show-toplevel').decode().strip())
    if output.is_relative_to(root):raise ValueError('ZIP must be outside the repository')
    if output.exists():raise ValueError('Refusing to overwrite an existing archive')
    if git('status','--porcelain'):raise ValueError('Commit/review source changes before packaging')
    if git('diff','--cached','--name-only'):raise ValueError('Staged changes require review')
    metadata=root/'.git'
    if not metadata.is_dir():raise ValueError('A standalone full-history checkout is required')
    active=[]
    env=APP/'.env'
    if env.is_file():
        for line in env.read_text().splitlines():
            if '=' not in line:continue
            key,value=line.split('=',1)
            if key in ('SECRET_KEY','OTP_HMAC_KEY','ADMIN_PASSWORD_HASH','DATABASE_URL','MIGRATION_DATABASE_URL','MIGRATION_DATABASE_PASSWORD','RUNTIME_DATABASE_PASSWORD'):
                value=value.strip().strip("\'\"").replace('$$','$')
                if len(value)>20:active.append(value.encode())
    private_admin=APP/'secrets/admin-access.json'
    if private_admin.exists():active.append(json.loads(private_admin.read_text())['password'].encode())
    process=subprocess.Popen(['git','cat-file','--batch-all-objects','--batch'],cwd=root,stdout=subprocess.PIPE)
    blobs=0
    while True:
        header=process.stdout.readline()
        if not header:break
        oid,kind,size=header.split();data=process.stdout.read(int(size));assert process.stdout.read(1)==b'\n'
        if kind==b'blob':
            blobs+=1
            if any(secret in data for secret in active):raise ValueError('Active private credential in Git history; stop for operator review')
    assert process.wait()==0
    tracked=git('ls-files','-z',cwd=root).decode().split('\0')
    files=[root/name for name in tracked if name]
    for file in files:
        if any(part in ('seed','logs','uploads','backups','secrets','.venv','__pycache__') for part in file.relative_to(root).parts) or file.name=='.env' or 'Zone.Identifier' in file.name:raise ValueError('Unwanted tracked artifact requires manual review')
    git_files=[path for path in metadata.rglob('*') if path.is_file()]
    for file in git_files:
        data=file.read_bytes()
        if any(secret in data for secret in active):raise ValueError('Active private credential in Git metadata')
        if file.name=='config' and re.search(rb'https?://[^/\s]*@',data):raise ValueError('Credential-bearing remote URL; operator must remove it before packaging')
    files+=git_files
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for file in sorted(files):
            if file.is_symlink():raise ValueError('Unexpected symlink requires review')
            archive.write(file,file.relative_to(root))
    output.chmod(0o600)
    history=git('log','--format=%H')
    with tempfile.TemporaryDirectory(prefix='sewasetu-unzip-check-') as temporary,zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        target=Path(temporary)
        for member in archive.infolist():
            path=(target/member.filename).resolve()
            if not path.is_relative_to(target):raise ValueError('Unsafe ZIP member')
            archive.extract(member,target)
            mode=stat.S_IMODE(member.external_attr>>16)
            if mode:path.chmod(mode)
        assert git('log','--format=%H',cwd=target)==history,'ZIP history mismatch'
        assert git('status','--porcelain',cwd=target)==b'','Unzipped checkout differs'
        git('fsck','--full',cwd=target)
    print(json.dumps({'zip':str(output),'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
        'head':git('rev-parse','HEAD').decode().strip(),'commits':len(history.splitlines()),'history_blobs_audited':blobs,'full_git_included':True,
        'unzip_history_clean_checkout_verified':True,'agent_docs_omitted_submission_gap':True}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();package(args.output.resolve())
