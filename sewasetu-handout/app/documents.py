"""Bounded private uploads; original filenames never become server paths."""
import os
from pathlib import Path
import secrets
import subprocess
import sys

LIMIT=5242880


class DocumentError(Exception):
    def __init__(self,message,status=422):self.message,self.status=message,status


def receive(file,directory):
    if not file or not file.filename:raise DocumentError('Choose a JPG, JPEG or PDF document.')
    extension=Path(file.filename).suffix.lower()
    if extension not in ('.jpg','.jpeg','.pdf'):
        raise DocumentError('Choose a JPG, JPEG or PDF document.',415)
    expected='application/pdf' if extension=='.pdf' else 'image/jpeg'
    if file.mimetype not in ('',expected,'application/octet-stream'):
        raise DocumentError('The document type and filename disagree. Choose the original JPG or PDF.',415)
    directory=Path(directory).resolve();directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    temporary=directory/('.upload-'+secrets.token_hex(24)+'.part')
    target=directory/(secrets.token_hex(24)+extension)
    try:
        size=0
        descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'wb') as output:
            while True:
                chunk=file.stream.read(65536)
                if not chunk:break
                size+=len(chunk)
                if size>LIMIT:raise DocumentError('The document is larger than 5MiB (5,242,880 bytes). Choose a smaller file.',413)
                output.write(chunk)
            output.flush();os.fsync(output.fileno())
        if not size:raise DocumentError('The document is empty. Choose the complete original file.')
        try:
            result=subprocess.run([sys.executable,str(Path(__file__).with_name('validate_document.py')),str(temporary),extension],
                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL,timeout=4,env={'PATH':os.environ.get('PATH',''),'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'})
        except subprocess.TimeoutExpired:
            raise DocumentError('The document is too complex to check. Choose a clear JPG or a simpler unencrypted PDF.') from None
        if result.returncode or result.stdout.strip()!=b'VALID':
            raise DocumentError('The document could not be read. Choose a complete JPG or an unencrypted PDF; your other details are saved.')
        os.replace(temporary,target)
        return str(target)
    finally:
        if temporary.exists():temporary.unlink()


def available(filename,directory):
    if not filename:return False
    try:
        path=Path(filename).resolve()
        return path.is_relative_to(Path(directory).resolve()) and path.suffix.lower() in ('.pdf','.jpg','.jpeg') and path.is_file()
    except (ValueError,OSError):return False
