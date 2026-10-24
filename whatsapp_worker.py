"""Run as a SINGLE dedicated Render background worker: python whatsapp_worker.py.

Messages are claimed from PostgreSQL with SKIP LOCKED. Unknown network outcomes
are not retried automatically to avoid duplicate WhatsApp notifications.
"""
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

# Import initializes hostel database tables and WhatsApp tables.
import app  # noqa: F401
from database import db, IS_POSTGRES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("whatsapp_worker")

def claim_one():
    conn = db()
    try:
        if IS_POSTGRES:
            row = conn.execute("""SELECT d.id,d.student_id,d.phone,c.template_name,c.language
                FROM whatsapp_deliveries d
                JOIN whatsapp_campaigns c ON c.id=d.campaign_id
                JOIN students s ON s.id=d.student_id
                JOIN whatsapp_optins o ON o.student_id=s.id
                WHERE d.status='queued' AND s.status='Active' AND o.consent=1
                ORDER BY d.id LIMIT 1 FOR UPDATE OF d SKIP LOCKED""").fetchone()
        else:
            # SQLite: run exactly one worker, never concurrent workers.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""SELECT d.id,d.student_id,d.phone,c.template_name,c.language
                FROM whatsapp_deliveries d
                JOIN whatsapp_campaigns c ON c.id=d.campaign_id
                JOIN students s ON s.id=d.student_id
                JOIN whatsapp_optins o ON o.student_id=s.id
                WHERE d.status='queued' AND s.status='Active' AND o.consent=1
                ORDER BY d.id LIMIT 1""").fetchone()
        if row:
            conn.execute("UPDATE whatsapp_deliveries SET status='processing',updated_at=CURRENT_TIMESTAMP WHERE id=?", (row['id'],))
        conn.commit()
        return dict(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def send_one(row):
    token = os.environ["WA_ACCESS_TOKEN"]
    number_id = os.environ["WA_PHONE_NUMBER_ID"]
    version = os.environ["WA_GRAPH_VERSION"]
    if not re.fullmatch(r"v\d+\.\d+", version) or not re.fullmatch(r"\d+", number_id):
        raise ValueError("Invalid WA_GRAPH_VERSION or WA_PHONE_NUMBER_ID")
    # Recheck consent just before the external request.
    conn = db()
    try:
        allowed = conn.execute("""SELECT 1 FROM students s
            JOIN whatsapp_optins o ON o.student_id=s.id
            WHERE s.id=? AND s.status='Active' AND o.consent=1""", (row["student_id"],)).fetchone()
    finally:
        conn.close()
    if not allowed:
        return "skipped", None, "Consent withdrawn or student inactive"

    url = f"https://graph.facebook.com/{version}/{number_id}/messages"
    payload = {"messaging_product":"whatsapp","to":row["phone"],"type":"template",
               "template":{"name":row["template_name"],"language":{"code":row["language"]}}}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            result=json.load(response)
        mid=(result.get("messages") or [{}])[0].get("id")
        return ("accepted",mid,None) if mid else ("unknown",None,"Meta returned no message ID")
    except urllib.error.HTTPError as exc:
        # Avoid logging response body, token, phone, or sensitive Meta details.
        return "failed",None,f"Meta HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        # Network timeout might occur after Meta accepted the request: no automatic retry.
        return "unknown",None,type(exc).__name__

def run_once():
    row=claim_one()
    if not row:
        return False
    try:
        status,mid,error=send_one(row)
    except Exception as exc:
        LOG.exception("Delivery %s encountered an error",row["id"])
        status,mid,error="unknown",None,type(exc).__name__
    conn=db()
    try:
        conn.execute("""UPDATE whatsapp_deliveries
            SET status=CASE WHEN status IN ('sent','delivered','read','failed') THEN status ELSE ? END,
                message_id=COALESCE(message_id,?),
                error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (status,mid,error,row["id"]))
        conn.commit()
    finally:
        conn.close()
    LOG.info("Delivery %s: %s",row["id"],status)
    return True

if __name__=="__main__":
    required=("WA_ACCESS_TOKEN","WA_PHONE_NUMBER_ID","WA_GRAPH_VERSION","WA_APP_SECRET","WA_VERIFY_TOKEN")
    missing=[key for key in required if not os.getenv(key)]
    if missing:
        raise SystemExit("Worker not started; missing required environment settings: "+", ".join(missing))
    while True:
        try:
            if not run_once():
                time.sleep(5)
        except Exception:
            LOG.exception("Worker loop failed; will retry")
            time.sleep(10)
