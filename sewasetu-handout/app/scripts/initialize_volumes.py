"""One-shot volume ownership; no database or citizen-value changes."""
import os
from pathlib import Path
for root in ['/var/sewasetu/uploads','/var/sewasetu/gateway']:
    base=Path(root);base.mkdir(parents=True,exist_ok=True)
    for directory,dirs,files in os.walk(base,followlinks=False):
        for path in [Path(directory),*(Path(directory)/name for name in dirs+files)]:
            if path.is_symlink():raise RuntimeError('Unexpected volume symlink')
            info=path.stat()
            if (info.st_uid,info.st_gid)!=(10001,10001):os.chown(path,10001,10001)
    os.chmod(base,0o700)
print('Private runtime volumes ready')
