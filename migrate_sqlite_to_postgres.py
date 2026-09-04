"""One-time migration of the existing hostel.db records into PostgreSQL."""
import os
import sqlite3
from pathlib import Path

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError as exc:
    raise SystemExit("Install requirements first: pip install -r requirements.txt") from exc

BASE = Path(__file__).parent
SOURCE = BASE / "hostel.db"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if not SOURCE.exists():
    raise SystemExit(f"Source database not found: {SOURCE}")
if not DATABASE_URL.startswith(("postgres://", "postgresql://")):
    raise SystemExit("Set DATABASE_URL to the PostgreSQL/Supabase connection string first.")

src = sqlite3.connect(SOURCE)
src.row_factory = sqlite3.Row
dst = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
tables = ["users", "rooms", "students", "payments", "enquiries"]

try:
    with dst.cursor() as cur:
        existing = {}
        for table in tables:
            cur.execute(f"SELECT COUNT(*) AS c FROM {table}")
            existing[table] = cur.fetchone()["c"]
        nonempty = {k: v for k, v in existing.items() if v}
        safe_starter_only = nonempty == {"users": 1}
        if nonempty and not safe_starter_only and os.environ.get("ALLOW_NONEMPTY_MIGRATION") != "1":
            raise SystemExit(f"Destination is not empty: {nonempty}. Migration stopped for safety.")

        cur.execute("DELETE FROM payments")
        cur.execute("DELETE FROM enquiries")
        cur.execute("DELETE FROM students")
        cur.execute("DELETE FROM rooms")
        cur.execute("DELETE FROM users")

        for table in tables:
            rows = src.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            if not rows:
                print(f"{table}: 0")
                continue
            cols = list(rows[0].keys())
            placeholders = ",".join(["%s"] * len(cols))
            sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
            for row in rows:
                cur.execute(sql, [row[c] for c in cols])
            print(f"{table}: {len(rows)}")

        for table in tables:
            cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}','id'), GREATEST(COALESCE((SELECT MAX(id) FROM {table}), 1), 1), true)")
    dst.commit()
    print("Migration completed successfully.")
except Exception:
    dst.rollback()
    raise
finally:
    src.close()
    dst.close()
