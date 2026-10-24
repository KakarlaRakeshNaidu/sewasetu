"""Owned PostgreSQL 15 sandbox; never accepts an external database URL."""
import os
from contextlib import closing
import subprocess
import tempfile
from pathlib import Path

import psycopg2


class PostgresSandbox:
    def __enter__(self):
        configured = os.environ.get("PG_BIN_DIR")
        self.bin = Path(configured or subprocess.check_output(["pg_config", "--bindir"], text=True).strip()).resolve()
        version = subprocess.check_output([str(self.bin / "postgres"), "--version"], text=True)
        if " 15." not in version:
            raise RuntimeError("PostgreSQL 15 required; set PG_BIN_DIR to its bin directory")
        self.version = version.strip()
        self.temp = tempfile.TemporaryDirectory(prefix="sewasetu-pg-")
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        self.data = self.root / "data"
        self.socket = self.root / "socket"
        self.socket.mkdir(mode=0o700)
        self.running = False
        try:
            command = [str(self.bin / "initdb"), "-D", str(self.data), "-U", "sewasetu_test",
                       "--auth=trust", "--no-locale", "--encoding=UTF8"]
            if os.environ.get("PG_SHARE_DIR"):
                command += ["-L", os.environ["PG_SHARE_DIR"]]
            self.run(command)
            with (self.data / "postgresql.conf").open("a") as config:
                config.write("\nlisten_addresses = ''\nunix_socket_permissions = 0700\n")
                config.write("timezone = 'Asia/Kolkata'\nmax_connections = 30\nshared_buffers = '32MB'\n")
                config.write("unix_socket_directories = '" + str(self.socket).replace("'", "''") + "'\n")
            self.run([str(self.bin / "pg_ctl"), "-D", str(self.data), "-l", str(self.root / "postgres.log"), "-w", "start"])
            self.running = True
            self.connection = dict(host=str(self.socket), port=5432, user="sewasetu_test", dbname="postgres")
            with closing(self.connect()) as conn, conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT current_setting('server_version_num')::int")
                    assert 150000 <= cur.fetchone()[0] < 160000
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    @staticmethod
    def run(args, *, sensitive=False):
        result = subprocess.run(args, capture_output=True, text=True, timeout=60)
        if result.returncode:
            detail = "SQL restore failed; inspect privately" if sensitive else result.stderr[-2000:]
            raise RuntimeError("PostgreSQL sandbox command failed: " + detail)

    def connect(self, **options):
        return psycopg2.connect(**self.connection, **options)

    def load(self, sql_path):
        assert self.root in self.socket.parents
        self.run([str(self.bin / "psql"), "-X", "-v", "ON_ERROR_STOP=1", "-h", str(self.socket),
                  "-U", "sewasetu_test", "-d", "postgres", "-f", str(sql_path)], sensitive=True)

    def __exit__(self, *exc):
        if self.running:
            self.run([str(self.bin / "pg_ctl"), "-D", str(self.data), "-m", "fast", "-w", "stop"])
            self.running = False
        self.temp.cleanup()
