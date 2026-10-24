"""Admin-controlled WhatsApp template messaging via Meta Cloud API."""
import os, re, json, hmac, hashlib, secrets, urllib.request, urllib.error
from flask import request, render_template, redirect, url_for, flash, session, abort, jsonify
from database import db, IS_POSTGRES

def normalize_ghana(value):
    digits = re.sub(r'\D', '', value or '')
    if len(digits) == 10 and digits.startswith('0'): digits = '233' + digits[1:]
    if len(digits) == 12 and digits.startswith('233'): return digits
    return None

def install_whatsapp(app_module):
    app = app_module.app
    def ensure_schema():
        conn = db()
        pk = "SERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
        conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS whatsapp_optins (
          student_id INTEGER PRIMARY KEY REFERENCES students(id),
          consent INTEGER NOT NULL DEFAULT 0,
          recorded_by TEXT, recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS whatsapp_campaigns (
          id {pk}, sender TEXT NOT NULL, scope TEXT NOT NULL,
          template_name TEXT NOT NULL, language TEXT NOT NULL,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS whatsapp_deliveries (
          id {pk}, campaign_id INTEGER REFERENCES whatsapp_campaigns(id),
          student_id INTEGER REFERENCES students(id), phone TEXT,
          message_id TEXT, status TEXT NOT NULL, error TEXT,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)
        conn.commit()
        conn.close()
    ensure_schema()

    def permitted():
        return app_module.has_permission('whatsapp.send')
    def csrf():
        if '_wa_csrf' not in session: session['_wa_csrf'] = secrets.token_urlsafe(32)
        return session['_wa_csrf']
    def verify_csrf():
        if not hmac.compare_digest(request.form.get('_csrf',''), session.get('_wa_csrf','')):
            abort(400)
    def students_for(scope, student_id):
        conn = db()
        if scope == 'individual':
            rows = conn.execute("""SELECT s.id,s.first_name,s.last_name,s.phone,s.room_number,
                COALESCE(w.consent,0) consent FROM students s LEFT JOIN whatsapp_optins w ON w.student_id=s.id
                WHERE s.status='Active' AND s.id=?""", (student_id,)).fetchall()
        elif scope == 'all':
            rows = conn.execute("""SELECT s.id,s.first_name,s.last_name,s.phone,s.room_number,
                COALESCE(w.consent,0) consent FROM students s LEFT JOIN whatsapp_optins w ON w.student_id=s.id
                WHERE s.status='Active' ORDER BY s.last_name,s.first_name""").fetchall()
        else:
            rows = []
        conn.close()
        return rows

    @app.route('/whatsapp', methods=['GET'])
    @app_module.login_required
    @app_module.permission_required('whatsapp.send')
    def whatsapp_center():
        conn = db()
        students = conn.execute("""SELECT s.id,s.first_name,s.last_name,s.room_number,s.phone,
            COALESCE(w.consent,0) consent FROM students s LEFT JOIN whatsapp_optins w ON w.student_id=s.id
            WHERE s.status='Active' ORDER BY s.last_name,s.first_name""").fetchall()
        history = conn.execute("""SELECT id,sender,scope,template_name,created_at FROM whatsapp_campaigns
            ORDER BY id DESC LIMIT 30""").fetchall()
        deliveries = conn.execute('''SELECT d.id,d.campaign_id,d.phone,d.status,d.error,d.updated_at,\n            s.first_name,s.last_name FROM whatsapp_deliveries d\n            LEFT JOIN students s ON s.id=d.student_id ORDER BY d.id DESC LIMIT 100''').fetchall()\n        conn.close()\n        return render_template('whatsapp_center.html',students=students,history=history,deliveries=deliveries,csrf=csrf(),
            ready=bool(os.getenv('WA_ACCESS_TOKEN') and os.getenv('WA_PHONE_NUMBER_ID') and os.getenv('WA_GRAPH_VERSION')),
            is_admin=session.get('role')=='admin')

    @app.route('/whatsapp/consent/<int:sid>', methods=['POST'])
    @app_module.login_required
    @app_module.admin_required
    def whatsapp_consent(sid):
        verify_csrf()
        conn=db()
        exists=conn.execute('SELECT 1 FROM students WHERE id=?',(sid,)).fetchone()
        if not exists: conn.close(); abort(404)
        consent=int(request.form.get('consent')=='1')
        conn.execute("""INSERT INTO whatsapp_optins(student_id,consent,recorded_by)
            VALUES(?,?,?) ON CONFLICT(student_id) DO UPDATE SET
            consent=excluded.consent,recorded_by=excluded.recorded_by,recorded_at=CURRENT_TIMESTAMP""",
            (sid,consent,session['user']))
        app_module.audit(conn,'whatsapp_consent','student',sid,'consent='+str(consent))
        conn.commit();conn.close()
        flash('Consent updated. Record consent only after student agrees.','success')
        return redirect(url_for('whatsapp_center'))

    @app.route('/whatsapp/send', methods=['POST'])
    @app_module.login_required
    @app_module.permission_required('whatsapp.send')
    def whatsapp_send():
        verify_csrf()
        if not (os.getenv('WA_ACCESS_TOKEN') and os.getenv('WA_PHONE_NUMBER_ID') and os.getenv('WA_GRAPH_VERSION')):
            flash('Meta Cloud API is not configured. No messages sent.','danger')
            return redirect(url_for('whatsapp_center'))
        scope=request.form.get('scope')
        if scope not in ('all','individual'): abort(400)
        try: sid=int(request.form.get('student_id','0'))
        except ValueError: abort(400)
        template=request.form.get('template_name','').strip()
        language=request.form.get('language','en_US').strip()
        if not re.fullmatch(r'[a-z0-9_]{1,100}',template) or not re.fullmatch(r'[a-z]{2}(?:_[A-Z]{2})?',language):
            flash('Enter a valid approved template name and language.','danger')
            return redirect(url_for('whatsapp_center'))
        if request.form.get('confirm')!='SEND':
            flash('Type SEND to confirm.','danger')
            return redirect(url_for('whatsapp_center'))
        targets=[(s,normalize_ghana(s['phone'])) for s in students_for(scope,sid) if s['consent']]
        targets=[(s,phone) for s,phone in targets if phone]
        # Cap prevents accidental mass sends; messages are one API request per opted-in student.
        if not targets or len(targets)>250:
            flash('No opted-in active students with valid Ghana phone numbers, or over 250 recipients.','danger')
            return redirect(url_for('whatsapp_center'))
        # Queue database records only. A separate worker performs API requests.
        # Never perform a potentially long bulk send inside a Render web request.
        conn=db()
        try:
            if IS_POSTGRES:
                row=conn.execute('INSERT INTO whatsapp_campaigns(sender,scope,template_name,language) VALUES(?,?,?,?) RETURNING id',
                    (session['user'],scope,template,language)).fetchone()
                campaign=row['id']
            else:
                cursor=conn.execute('INSERT INTO whatsapp_campaigns(sender,scope,template_name,language) VALUES(?,?,?,?)',
                    (session['user'],scope,template,language))
                campaign=cursor.lastrowid
            for student,phone in targets:
                conn.execute('INSERT INTO whatsapp_deliveries(campaign_id,student_id,phone,status) VALUES(?,?,?,?)',
                    (campaign,student['id'],phone,'queued'))
            app_module.audit(conn,'whatsapp_campaign','campaign',campaign,'queued='+str(len(targets)))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        flash(f'Queued {len(targets)} messages. A configured background worker will send them.','success')
        return redirect(url_for('whatsapp_center'))

    @app.route('/whatsapp/webhook', methods=['GET','POST'])
    def whatsapp_webhook():
        if request.method=='GET':
            token=os.getenv('WA_VERIFY_TOKEN','')
            if token and hmac.compare_digest(request.args.get('hub.verify_token',''),token) and request.args.get('hub.mode')=='subscribe':
                return request.args.get('hub.challenge',''),200
            abort(403)
        secret=os.getenv('WA_APP_SECRET','')
        signature=request.headers.get('X-Hub-Signature-256','')
        expected='sha256='+hmac.new(secret.encode(),request.get_data(),hashlib.sha256).hexdigest()
        if not secret or not hmac.compare_digest(signature,expected): abort(403)
        body=request.get_json(silent=True) or {}
        conn=db()
        for entry in body.get('entry',[]):
            for change in entry.get('changes',[]):
                for item in change.get('value',{}).get('statuses',[]):
                    mid=item.get('id'); status=item.get('status')
                    if mid and status in ('sent','delivered','read','failed'):
                        conn.execute('UPDATE whatsapp_deliveries SET status=?,updated_at=CURRENT_TIMESTAMP WHERE message_id=?',(status,mid))
        conn.commit();conn.close()
        return jsonify({'ok':True})
