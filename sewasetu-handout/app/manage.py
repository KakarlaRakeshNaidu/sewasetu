"""Private operator commands. Never reads defaults or restores/reseeds data."""
import argparse
import os
from pathlib import Path
import psycopg2
from psycopg2 import sql
from sqlalchemy.engine import make_url


def connect():
    value = make_url(os.environ['MIGRATION_DATABASE_URL'])
    if value.get_backend_name() != 'postgresql':
        raise RuntimeError('PostgreSQL required')
    return psycopg2.connect(value.set(drivername='postgresql').render_as_string(hide_password=False))


def migrate(connection):
    with connection.cursor() as cur:
        cur.execute('SELECT pg_advisory_lock(72130981)')
        try:
            cur.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())')
            connection.commit()
            for path in sorted((Path(__file__).parent/'migrations').glob('*.sql')):
                cur.execute('SELECT 1 FROM schema_migrations WHERE version=%s',(path.name,))
                if cur.fetchone():
                    continue
                # Files are standalone atomic SQL; include the version marker in that same transaction.
                source = path.read_text().removesuffix('\n').removesuffix('COMMIT;')
                cur.execute(source)
                cur.execute('INSERT INTO schema_migrations(version) VALUES(%s)',(path.name,))
                connection.commit()
                print('Applied '+path.name)
        finally:
            connection.rollback()
            cur.execute('SELECT pg_advisory_unlock(72130981)')
            connection.commit()


def rotate(connection):
    runtime_password = os.environ['RUNTIME_DATABASE_PASSWORD']
    owner_password = os.environ['MIGRATION_DATABASE_PASSWORD']
    if min(len(runtime_password),len(owner_password)) < 32 or runtime_password == owner_password:
        raise RuntimeError('Use independent generated passwords of at least 32 characters')
    with connection, connection.cursor() as cur:
        cur.execute('SELECT current_user,current_database()')
        owner, database = cur.fetchone()
        if owner == 'portal_runtime':
            raise RuntimeError('Use a separate owner/migration account')
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname='portal_runtime'")
        if not cur.fetchone():
            cur.execute('CREATE ROLE portal_runtime LOGIN')
        cur.execute('ALTER ROLE portal_runtime NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT PASSWORD %s',(runtime_password,))
        cur.execute(sql.SQL('ALTER ROLE {} PASSWORD %s').format(sql.Identifier(owner)),(owner_password,))
        cur.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO portal_runtime').format(sql.Identifier(database)))
        cur.execute('GRANT USAGE ON SCHEMA public TO portal_runtime')
        cur.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        cur.execute('GRANT SELECT,INSERT,UPDATE ON applications TO portal_runtime')
        cur.execute('GRANT SELECT,INSERT ON portal_users,application_audit TO portal_runtime')
        cur.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON web_sessions,portal_challenges,application_drafts TO portal_runtime')
        cur.execute('GRANT SELECT,INSERT,UPDATE ON notification_outbox TO portal_runtime')
        cur.execute('GRANT USAGE ON SEQUENCE notification_outbox_id_seq TO portal_runtime')
        cur.execute('GRANT USAGE ON SEQUENCE applications_id_seq,application_audit_id_seq TO portal_runtime')
    print('Owner password rotated; restricted portal_runtime role configured. Update private operator and runtime environments.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['migrate','rotate-roles'])
    args = parser.parse_args()
    connection = connect()
    try:
        (migrate if args.command == 'migrate' else rotate)(connection)
    finally:
        connection.close()


if __name__ == '__main__':
    main()
