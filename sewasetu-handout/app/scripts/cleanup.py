"""Bounded expiry cleanup; never deletes accepted attachments or citizen records."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from runtime import settings,Database


def main():
    db=Database(settings())
    conn=db.connect()
    try:
        with conn,conn.cursor() as cur:
            for table,column in [('web_sessions','expires_at'),('portal_challenges','expires_at')]:
                cur.execute('DELETE FROM '+table+' WHERE ctid IN (SELECT ctid FROM '+table+' WHERE '+column+"<now()-interval '1 day' LIMIT 1000)")
    finally:
        conn.close()
        db.engine.dispose()


if __name__=='__main__':
    main()
