import os
import sqlite3
from pathlib import Path

BASE = Path(__file__).parent
SQLITE_DB = BASE / "hostel.db"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_POSTGRES = DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:  # Local SQLite use still works without PostgreSQL driver.
    psycopg2 = None
    RealDictCursor = None


class DBConnection:
    def __init__(self):
        self.is_postgres = IS_POSTGRES
        if self.is_postgres:
            if psycopg2 is None:
                raise RuntimeError("PostgreSQL configured but psycopg2 is not installed.")
            self.conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        else:
            self.conn = sqlite3.connect(SQLITE_DB)
            self.conn.row_factory = sqlite3.Row

    def _sql(self, sql):
        return sql.replace("?", "%s") if self.is_postgres else sql

    def execute(self, sql, params=()):
        cur = self.conn.cursor()
        if self.is_postgres and sql.strip().lower().startswith("select last_insert_rowid()"):
            cur.execute("SELECT currval(pg_get_serial_sequence('payments','id')) AS id")
        else:
            cur.execute(self._sql(sql), params)
        return cur

    def executescript(self, script):
        if self.is_postgres:
            cur = self.conn.cursor()
            cur.execute(script)
            return cur
        return self.conn.executescript(script)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


def db():
    return DBConnection()


def integrity_error_types():
    types = [sqlite3.IntegrityError]
    if psycopg2 is not None:
        types.append(psycopg2.IntegrityError)
    return tuple(types)
