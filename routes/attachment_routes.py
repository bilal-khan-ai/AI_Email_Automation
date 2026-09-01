# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - Attachment & Media Routes Blueprint - start
"""
routes/attachment_routes.py

Attachment & media HTTP routes:
- Inline image uploading & serving (/api/upload_inline_image, /api/inline_image/<filename>)
- Content-addressable attachment preview & thumbnail caching (/api/view_attachment)
- Attachment downloading (/api/download_attachment)
- DevTools 404 suppressor (/.well-known/appspecific/com.chrome.devtools.json)
"""

import os
import io
import re
import uuid
import base64
import logging
import hashlib
import mimetypes
from werkzeug.utils import secure_filename
from flask import Blueprint, jsonify, request, send_file, send_from_directory, make_response

from auth import login_required
from config import Config
from dashboard_context import (
    DATA_DIR, INLINE_UPLOAD_FOLDER, ATTACHMENT_HASH_MAP, graph_connector
)

logger = logging.getLogger(__name__)

attachment_bp = Blueprint('attachments', __name__)


@attachment_bp.route('/.well-known/appspecific/com.chrome.devtools.json')
def silent_404():
    """Silent route for browser-specific devtools requests to reduce log noise."""
    return jsonify({'error': 'Not Found'}), 404


@attachment_bp.route('/api/upload_inline_image', methods=['POST'])
@login_required
def upload_inline_image():
    """Upload an inline image pasted or dropped into the Tiptap editor."""
    try:
        file = request.files.get('file') or request.files.get('image')
        if not file:
            data = request.get_json(silent=True) or {}
            base64_str = data.get('image_base64') or data.get('data')
            if base64_str:
                match = re.match(r'^data:(image/[a-zA-Z0-9\-\+\.]+);base64,(.+)$', base64_str)
                if match:
                    ct = match.group(1)
                    raw_bytes = base64.b64decode(match.group(2))
                    ext = ct.split('/')[-1].replace('jpeg', 'jpg')
                    uid = uuid.uuid4().hex
                    fname = f"{uid}.{ext}"
                    filepath = os.path.join(INLINE_UPLOAD_FOLDER, fname)
                    with open(filepath, 'wb') as f:
                        f.write(raw_bytes)
                    cid = f"inline_img_{uid[:12]}"
                    return jsonify({
                        'url': f"/api/inline_image/{fname}",
                        'cid': cid,
                        'filename': fname,
                        'name': fname,
                        'contentType': ct
                    })
            return jsonify({'error': 'No image provided'}), 400

        original_filename = secure_filename(file.filename or 'image.png')
        ext = original_filename.rsplit('.', 1)[-1].lower() if '.' in original_filename else 'png'
        uid = uuid.uuid4().hex
        stored_filename = f"{uid}.{ext}"
        filepath = os.path.join(INLINE_UPLOAD_FOLDER, stored_filename)
        file.save(filepath)

        content_type = file.content_type or f"image/{ext}"
        cid = f"inline_img_{uid[:12]}"

        return jsonify({
            'url': f"/api/inline_image/{stored_filename}",
            'cid': cid,
            'filename': stored_filename,
            'name': original_filename,
            'contentType': content_type
        })
    except Exception as e:
        logger.error(f"Error uploading inline image: {e}")
        return jsonify({'error': str(e)}), 500


@attachment_bp.route('/api/inline_image/<filename>')
def serve_inline_image(filename):
    """Serve uploaded inline image with caching headers."""
    try:
        safe_filename = secure_filename(filename)
        return send_from_directory(INLINE_UPLOAD_FOLDER, safe_filename, max_age=86400)
    except Exception as e:
        logger.error(f"Error serving inline image {filename}: {e}")
        return jsonify({'error': 'Image not found'}), 404


@attachment_bp.route('/api/view_attachment', methods=['GET'])
@login_required
def view_attachment():
    """
    Serve attachment for inline viewing or preview with SHA-256 content-addressable deduplication and thumbnail caching.
    """
    try:
        user_email = request.args.get('user_email') or Config.USER_EMAIL
        message_id = request.args.get('message_id')
        attachment_id = request.args.get('attachment_id')
        filename = request.args.get('filename') or request.args.get('attachment_name', 'attachment')
        is_thumbnail = request.args.get('thumbnail', 'false').lower() in ('true', '1')

        if not message_id or not attachment_id:
            return jsonify({'error': 'Missing message_id or attachment_id'}), 400

        thumb_dir = os.path.join(DATA_DIR, 'thumbnails')
        os.makedirs(thumb_dir, exist_ok=True)
        lookup_key = f"{message_id}_{attachment_id}"

        # 1. Fast Path: Check in-memory hash map for known attachment thumbnail
        if is_thumbnail and lookup_key in ATTACHMENT_HASH_MAP:
            cached_hash = ATTACHMENT_HASH_MAP[lookup_key]
            thumb_path = os.path.join(thumb_dir, f"{cached_hash}.webp")
            if os.path.exists(thumb_path):
                resp = make_response(send_file(thumb_path, mimetype='image/webp', as_attachment=False))
                resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
                resp.headers['ETag'] = f'"{cached_hash}"'
                return resp

        # 2. Download attachment data from Graph
        attachment_data = graph_connector.get_attachment_sync(
            user_email=user_email,
            message_id=message_id,
            attachment_id=attachment_id
        )

        if not attachment_data:
            logger.error(f"Failed to download attachment data | User: {user_email} | Msg: {message_id[:15]}... | Att: {attachment_id[:15]}...")
            return jsonify({'error': 'Attachment not found or failed to download'}), 404

        # 3. Compute SHA-256 content hash
        content_hash = hashlib.sha256(attachment_data).hexdigest()
        ATTACHMENT_HASH_MAP[lookup_key] = content_hash

        # Priority: 1. Query parameter, 2. Guess from filename, 3. Default
        mimetype = request.args.get('content_type')
        if mimetype:
            mimetype = mimetype.split('@')[0].strip()
        if not mimetype or '/' not in mimetype:
            mimetype, _ = mimetypes.guess_type(filename)
        
        if not mimetype:
            mimetype = 'application/octet-stream'

        # 4. Handle Thumbnail request with deduplicated storage
        if is_thumbnail:
            thumb_path = os.path.join(thumb_dir, f"{content_hash}.webp")

            if not os.path.exists(thumb_path):
                try:
                    from PIL import Image
                    with Image.open(io.BytesIO(attachment_data)) as img:
                        img.thumbnail((260, 260))
                        if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                            img = img.convert('RGBA')
                        else:
                            img = img.convert('RGB')
                        img.save(thumb_path, 'WEBP', quality=80, optimize=True)
                except Exception as thumb_err:
                    logger.warning(f"Thumbnail generation failed: {thumb_err}")

            if os.path.exists(thumb_path):
                resp = make_response(send_file(thumb_path, mimetype='image/webp', as_attachment=False))
                resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
                resp.headers['ETag'] = f'"{content_hash}"'
                return resp

        # 5. Full Attachment Stream
        resp = make_response(send_file(
            io.BytesIO(attachment_data),
            mimetype=mimetype,
            as_attachment=False,
            download_name=secure_filename(filename) or 'attachment'
        ))
        resp.headers['Cache-Control'] = 'private, max-age=86400'
        resp.headers['ETag'] = f'"{content_hash}"'
        return resp

    except Exception as e:
        logger.error(f"Error viewing attachment: {e}")
        return jsonify({'error': f'Failed to view attachment: {str(e)}'}), 500


@attachment_bp.route('/api/download_attachment', methods=['GET'])
@login_required
def download_attachment():
    """Download attachment from Microsoft Graph with Content-Disposition attachment header."""
    try:
        user_email = request.args.get('user_email') or Config.USER_EMAIL
        message_id = request.args.get('message_id')
        attachment_id = request.args.get('attachment_id')
        attachment_name = request.args.get('attachment_name') or request.args.get('filename', 'attachment')

        if not message_id or not attachment_id:
            return jsonify({'error': 'Missing message_id or attachment_id'}), 400

        attachment_data = graph_connector.get_attachment_sync(
            user_email=user_email,
            message_id=message_id,
            attachment_id=attachment_id
        )

        if not attachment_data:
            logger.error(f"Failed to download attachment for download | User: {user_email} | Msg: {message_id[:15]}... | Att: {attachment_id[:15]}...")
            return jsonify({'error': 'Attachment failed to download'}), 404

        mimetype, _ = mimetypes.guess_type(attachment_name)
        if not mimetype:
            mimetype = 'application/octet-stream'

        return send_file(
            io.BytesIO(attachment_data),
            as_attachment=True,
            download_name=secure_filename(attachment_name) or 'attachment',
            mimetype=mimetype
        )
    except Exception as e:
        logger.error(f"Error downloading attachment: {e}")
        return jsonify({'error': f'Failed to download attachment: {str(e)}'}), 500

__all__ = ['attachment_bp']
# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - Attachment & Media Routes Blueprint - end
