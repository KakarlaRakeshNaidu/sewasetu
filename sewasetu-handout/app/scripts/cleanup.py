"""Bounded expiry cleanup, preserving all accepted records and their attachments."""
import os
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from runtime import settings,Database


def main():
    db=Database(settings());conn=db.connect()
    directory=Path(os.environ.get('UPLOAD_DIR',str(Path(__file__).resolve().parents[2]/'uploads'))).resolve()
    candidates=set()
    if directory.exists():
        for entry in directory.iterdir():
            if len(candidates)>=100:break
            if entry.is_file() and not entry.is_symlink() and entry.stat().st_mtime<time.time()-7*86400:
                if entry.name.startswith('.upload-') or entry.suffix.lower() in ('.pdf','.jpg','.jpeg'):
                    candidates.add(entry.resolve())
    try:
        with conn.cursor() as cur:
            for table,column in [('web_sessions','expires_at'),('portal_challenges','expires_at')]:
                cur.execute('DELETE FROM '+table+' WHERE ctid IN (SELECT ctid FROM '+table+' WHERE '+column+"<now()-interval '1 day' LIMIT 1000)")
            # Lock/recheck expired unaccepted drafts; acceptance locks the same row and rejects expiry.
            cur.execute("DELETE FROM application_drafts WHERE id IN (SELECT id FROM application_drafts WHERE submitted_application_id IS NULL AND expires_at<now() ORDER BY expires_at LIMIT 100 FOR UPDATE SKIP LOCKED)")
            if candidates:
                # Stream references; never load citizen records or names into operational logs.
                for statement in ["SELECT doc_path FROM applications WHERE doc_path<>''", "SELECT data->>'doc_path' FROM application_drafts WHERE data ? 'doc_path'", "SELECT data->'values'->>'doc_path' FROM application_drafts WHERE data ? 'values'", "SELECT changed_values->'doc_path'->>'before' FROM application_audit WHERE changed_values ? 'doc_path'", "SELECT changed_values->'doc_path'->>'after' FROM application_audit WHERE changed_values ? 'doc_path'"]:
                    cur.execute(statement)
                    while True:
                        batch=cur.fetchmany(1000)
                        if not batch:break
                        for row in batch:
                            if row[0]:candidates.discard(Path(row[0]).resolve())
        conn.commit()
        for path in candidates:
            if path.is_relative_to(directory) and path.is_file() and not path.is_symlink():path.unlink()
        print('Expiry cleanup completed; orphan files removed:',len(candidates))
    except BaseException:
        conn.rollback();raise
    finally:
        conn.close();db.engine.dispose()


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Expiry cleanup failed: '+type(error).__name__,file=sys.stderr)
        raise SystemExit(1)
