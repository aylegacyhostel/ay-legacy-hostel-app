import io
import json
from datetime import datetime, date

from flask import render_template, request, send_file, flash, redirect, url_for, session, abort

BACKUP_TABLES = ["rooms", "students", "payments", "enquiries"]
BACKUP_VERSION = 1
MAX_BACKUP_BYTES = 10 * 1024 * 1024


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _row_to_dict(row):
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


def _table_columns(conn, table, is_postgres):
    if is_postgres:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=? ORDER BY ordinal_position",
            (table,),
        ).fetchall()
        return [r["column_name"] for r in rows]
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r["name"] for r in rows]


def _reset_sequence(conn, table, is_postgres):
    if not is_postgres:
        return
    conn.execute(
        f"SELECT setval(pg_get_serial_sequence('{table}','id'), "
        f"COALESCE((SELECT MAX(id) FROM {table}), 1), "
        f"CASE WHEN EXISTS (SELECT 1 FROM {table}) THEN true ELSE false END)"
    )


def install_backup_restore(app_module):
    app = app_module.app
    db = app_module.db
    IS_POSTGRES = app_module.IS_POSTGRES
    audit = app_module.audit

    def _admin_guard():
        if "user" not in session:
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            abort(403)
        return None

    @app.route("/admin/backup-restore", methods=["GET"])
    def backup_restore():
        guard = _admin_guard()
        if guard:
            return guard
        conn = db()
        counts = {}
        for table in BACKUP_TABLES:
            counts[table] = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
        conn.close()
        return render_template("backup_restore.html", counts=counts)

    @app.route("/admin/backup/download", methods=["GET"])
    def download_backup():
        guard = _admin_guard()
        if guard:
            return guard

        conn = db()
        try:
            payload = {
                "app": "AY Legacy Hostel",
                "backup_version": BACKUP_VERSION,
                "created_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                "created_by": session.get("user"),
                "tables": {},
            }
            for table in BACKUP_TABLES:
                rows = conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
                payload["tables"][table] = [_row_to_dict(r) for r in rows]

            audit(conn, "backup_download", "system", details="Administrator downloaded business-data backup")
            conn.commit()
        finally:
            conn.close()

        data = json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default).encode("utf-8")
        filename = f"AY_Legacy_Hostel_Backup_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"
        return send_file(
            io.BytesIO(data),
            mimetype="application/json",
            as_attachment=True,
            download_name=filename,
        )

    @app.route("/admin/backup/restore", methods=["POST"])
    def restore_backup():
        guard = _admin_guard()
        if guard:
            return guard

        if request.form.get("confirm_restore") != "RESTORE":
            flash('Type RESTORE in the confirmation box before restoring a backup.', 'danger')
            return redirect(url_for("backup_restore"))

        upload = request.files.get("backup_file")
        if not upload or not upload.filename:
            flash("Choose an AY Legacy Hostel backup file first.", "danger")
            return redirect(url_for("backup_restore"))

        raw = upload.read(MAX_BACKUP_BYTES + 1)
        if len(raw) > MAX_BACKUP_BYTES:
            flash("Backup file is too large. Maximum allowed size is 10 MB.", "danger")
            return redirect(url_for("backup_restore"))

        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            flash("That file is not a valid JSON backup.", "danger")
            return redirect(url_for("backup_restore"))

        if payload.get("app") != "AY Legacy Hostel" or payload.get("backup_version") != BACKUP_VERSION:
            flash("This is not a supported AY Legacy Hostel backup file.", "danger")
            return redirect(url_for("backup_restore"))

        tables = payload.get("tables")
        if not isinstance(tables, dict) or any(t not in tables or not isinstance(tables[t], list) for t in BACKUP_TABLES):
            flash("Backup file is incomplete or damaged.", "danger")
            return redirect(url_for("backup_restore"))

        conn = db()
        try:
            # Delete child rows first so foreign-key relationships remain valid.
            for table in ["payments", "enquiries", "students", "rooms"]:
                conn.execute(f"DELETE FROM {table}")

            # Restore parents before children.
            for table in ["rooms", "students", "payments", "enquiries"]:
                allowed_columns = _table_columns(conn, table, IS_POSTGRES)
                for row in tables[table]:
                    if not isinstance(row, dict):
                        raise ValueError(f"Invalid row in {table}")
                    cols = [c for c in allowed_columns if c in row]
                    if not cols:
                        continue
                    placeholders = ",".join(["?"] * len(cols))
                    values = [row.get(c) for c in cols]
                    conn.execute(
                        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
                        values,
                    )
                _reset_sequence(conn, table, IS_POSTGRES)

            audit(
                conn,
                "backup_restore",
                "system",
                details=f"Restored backup created {payload.get('created_at', 'unknown')}",
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            flash(f"Restore failed. No changes were saved: {exc}", "danger")
            conn.close()
            return redirect(url_for("backup_restore"))
        conn.close()

        flash("Backup restored successfully. Student, room, payment and enquiry records were replaced with the backup data.", "success")
        return redirect(url_for("backup_restore"))
