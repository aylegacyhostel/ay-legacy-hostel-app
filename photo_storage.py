import json
import mimetypes
import os
import uuid
from datetime import datetime
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from flask import abort, redirect, request, send_from_directory, session
from werkzeug.utils import secure_filename


ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
ALLOWED_MIME_TYPES = {'image/png', 'image/jpeg', 'image/webp'}
MAX_PHOTO_BYTES = 5 * 1024 * 1024
PHOTO_PREFIX = 'sb:'


def _settings():
    return (
        os.environ.get('SUPABASE_URL', '').rstrip('/'),
        os.environ.get('SUPABASE_SERVICE_ROLE_KEY', ''),
        os.environ.get('SUPABASE_STORAGE_BUCKET', 'student-photos'),
    )


def storage_configured():
    url, key, _ = _settings()
    return bool(url and key)


def _request(method, path, data=None, content_type=None, allow_404=False):
    base_url, service_key, _ = _settings()
    if not base_url or not service_key:
        raise RuntimeError('Supabase Storage is not configured.')

    headers = {
        'apikey': service_key,
        'Authorization': f'Bearer {service_key}',
    }
    if content_type:
        headers['Content-Type'] = content_type

    req = Request(base_url + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except HTTPError as exc:
        body = exc.read().decode('utf-8', errors='replace')
        if allow_404 and exc.code == 404:
            return exc.code, body.encode()
        raise RuntimeError(f'Supabase Storage request failed ({exc.code}): {body}') from exc


def ensure_private_bucket():
    _, _, bucket = _settings()
    bucket_q = quote(bucket, safe='')
    status, _ = _request('GET', f'/storage/v1/bucket/{bucket_q}', allow_404=True)
    if status != 404:
        return

    payload = json.dumps({
        'id': bucket,
        'name': bucket,
        'public': False,
        'file_size_limit': MAX_PHOTO_BYTES,
        'allowed_mime_types': sorted(ALLOWED_MIME_TYPES),
    }).encode('utf-8')
    _request('POST', '/storage/v1/bucket', payload, 'application/json')


def upload_photo(file_storage):
    filename = secure_filename(file_storage.filename or '')
    if not filename or '.' not in filename:
        raise ValueError('Please choose a JPG, PNG or WEBP image.')

    ext = filename.rsplit('.', 1)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError('Only JPG, JPEG, PNG and WEBP images are allowed.')

    mime = (file_storage.mimetype or mimetypes.guess_type(filename)[0] or '').lower()
    if mime not in ALLOWED_MIME_TYPES:
        raise ValueError('The selected file is not a supported image type.')

    data = file_storage.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise ValueError('Student photo must be 5 MB or smaller.')

    ensure_private_bucket()
    _, _, bucket = _settings()
    object_name = (
        f"students/{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_"
        f"{uuid.uuid4().hex[:12]}_{filename}"
    )
    path = f"/storage/v1/object/{quote(bucket, safe='')}/{quote(object_name, safe='/')}"
    _request('POST', path, data, mime)
    return PHOTO_PREFIX + object_name


def signed_photo_url(photo_value, expires_in=3600):
    if not photo_value or not photo_value.startswith(PHOTO_PREFIX):
        return None

    base_url, _, bucket = _settings()
    object_name = photo_value[len(PHOTO_PREFIX):]
    payload = json.dumps({'expiresIn': int(expires_in)}).encode('utf-8')
    path = (
        f"/storage/v1/object/sign/{quote(bucket, safe='')}/"
        f"{quote(object_name, safe='/')}"
    )
    _, raw = _request('POST', path, payload, 'application/json')
    result = json.loads(raw.decode('utf-8'))
    signed = result.get('signedURL') or result.get('signedUrl')
    if not signed:
        raise RuntimeError('Supabase did not return a signed photo URL.')
    if signed.startswith('http://') or signed.startswith('https://'):
        return signed
    if signed.startswith('/storage/v1/'):
        return base_url + signed
    if signed.startswith('/object/'):
        return base_url + '/storage/v1' + signed
    return base_url + '/storage/v1/' + signed.lstrip('/')


def install_photo_storage(app_module):
    app = app_module.app

    def persistent_save_student_form(conn, sid=None):
        fields = [
            'student_id','first_name','last_name','gender','phone','email','school','program','year_level',
            'date_of_birth','hometown','room_number','bed_space','check_in_date','expected_check_out','status',
            'parent_name','parent_phone','parent_email','parent_relationship','emergency_name','emergency_phone',
            'emergency_relationship','medical_notes','id_type','id_number','notes'
        ]
        vals = [request.form.get(f, '').strip() for f in fields]
        photo = None
        upload = request.files.get('photo')

        if upload and upload.filename:
            if storage_configured():
                try:
                    photo = upload_photo(upload)
                except (ValueError, RuntimeError) as exc:
                    app_module.flash(str(exc), 'danger')
            else:
                if app_module.allowed_file(upload.filename):
                    local_name = (
                        f"{datetime.now().strftime('%Y%m%d%H%M%S')}_"
                        f"{secure_filename(upload.filename)}"
                    )
                    upload.save(app_module.UPLOADS / local_name)
                    photo = local_name
                    app_module.flash(
                        'Supabase photo storage is not configured. This photo is only temporary and may disappear after a redeploy.',
                        'warning'
                    )

        if sid:
            pairs = [f'{field}=?' for field in fields]
            if photo:
                pairs.append('photo=?')
                vals.append(photo)
            conn.execute(f"UPDATE students SET {','.join(pairs)} WHERE id=?", vals + [sid])
        else:
            if photo:
                fields.append('photo')
                vals.append(photo)
            conn.execute(
                f"INSERT INTO students({','.join(fields)}) VALUES({','.join(['?'] * len(fields))})",
                vals,
            )

    app_module.save_student_form = persistent_save_student_form

    def student_photo(sid):
        if 'user' not in session:
            return redirect(app_module.url_for('login'))

        conn = app_module.db()
        student = conn.execute('SELECT photo FROM students WHERE id=?', (sid,)).fetchone()
        conn.close()
        if not student or not student['photo']:
            abort(404)

        photo = student['photo']
        if photo.startswith(PHOTO_PREFIX):
            if not storage_configured():
                abort(503)
            try:
                return redirect(signed_photo_url(photo))
            except RuntimeError:
                abort(502)

        return send_from_directory(app_module.UPLOADS, photo)

    app.add_url_rule(
        '/students/<int:sid>/photo',
        endpoint='student_photo',
        view_func=student_photo,
        methods=['GET'],
    )

    app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024
