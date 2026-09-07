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


def compute_image_content_hash(data: bytes, is_image: bool = False) -> str:
    """
    Compute deterministic SHA-256 content hash.
    For images, decodes the raster pixels to strip all EXIF, XMP, IPTC, creation dates,
    and container metadata.
    For non-images, hashes the raw binary stream.
    """
    if is_image:
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as img:
                if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                    mode_img = img.convert('RGBA')
                else:
                    mode_img = img.convert('RGB')
                return hashlib.sha256(mode_img.tobytes()).hexdigest()
        except Exception:
            pass
    return hashlib.sha256(data).hexdigest()


@attachment_bp.route('/api/upload_inline_image', methods=['POST'])
@login_required
def upload_inline_image():
    """Upload an inline image pasted or dropped into the Tiptap editor with content-addressed caching."""
    try:
        file = request.files.get('file') or request.files.get('image')
        raw_bytes = None
        ct = None
        ext = 'png'
        original_filename = 'image.png'

        if file:
            original_filename = secure_filename(file.filename or 'image.png')
            raw_bytes = file.read()
            ct = file.content_type
            ext = original_filename.rsplit('.', 1)[-1].lower() if '.' in original_filename else 'png'
        else:
            data = request.get_json(silent=True) or {}
            base64_str = data.get('image_base64') or data.get('data')
            if base64_str:
                match = re.match(r'^data:(image/[a-zA-Z0-9\-\+\.]+);base64,(.+)$', base64_str)
                if match:
                    ct = match.group(1)
                    raw_bytes = base64.b64decode(match.group(2))
                    ext = ct.split('/')[-1].replace('jpeg', 'jpg')
        
        if not raw_bytes:
            return jsonify({'error': 'No image provided'}), 400

        # Compute content-addressed pixel hash (metadata-free)
        content_hash = compute_image_content_hash(raw_bytes, is_image=True)
        stored_filename = f"{content_hash}.{ext}"
        filepath = os.path.join(INLINE_UPLOAD_FOLDER, stored_filename)

        if not os.path.exists(filepath):
            with open(filepath, 'wb') as f:
                f.write(raw_bytes)

        cid = f"inline_img_{content_hash[:12]}"
        return jsonify({
            'url': f"/api/inline_image/{stored_filename}",
            'cid': cid,
            'filename': stored_filename,
            'name': original_filename,
            'contentType': ct or f"image/{ext}"
        })
    except Exception as e:
        logger.error(f"Error uploading inline image: {e}")
        return jsonify({'error': str(e)}), 500


@attachment_bp.route('/api/inline_image/<filename>')
def serve_inline_image(filename):
    """Serve uploaded inline image with immutable caching headers and 304 support."""
    try:
        safe_filename = secure_filename(filename)
        filepath = os.path.join(INLINE_UPLOAD_FOLDER, safe_filename)
        if not os.path.exists(filepath):
            return jsonify({'error': 'Image not found'}), 404

        etag = f'"{safe_filename.rsplit(".", 1)[0]}"'
        if_none_match = request.headers.get('If-None-Match')
        if if_none_match and if_none_match.strip() == etag:
            return '', 304

        resp = make_response(send_from_directory(INLINE_UPLOAD_FOLDER, safe_filename))
        resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        resp.headers['ETag'] = etag
        return resp
    except Exception as e:
        logger.error(f"Error serving inline image {filename}: {e}")
        return jsonify({'error': 'Image not found'}), 404


@attachment_bp.route('/api/view_attachment', methods=['GET'])
@login_required
def view_attachment():
    """
    Serve attachment for inline viewing or preview with content-addressed deduplication,
    metadata-free pixel hashing, and full HTTP caching (304 Not Modified).
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
        cache_dir = os.path.join(DATA_DIR, 'attachment_cache')
        os.makedirs(thumb_dir, exist_ok=True)
        os.makedirs(cache_dir, exist_ok=True)
        lookup_key = f"{message_id}_{attachment_id}"

        # 1. Fast Path: If client sent If-None-Match and it matches known hash, return 304 immediately
        cached_hash = ATTACHMENT_HASH_MAP.get(lookup_key)
        if_none_match = request.headers.get('If-None-Match')
        if cached_hash and if_none_match and if_none_match.strip('"') == cached_hash:
            return '', 304

        # 2. Fast Path: Check in-memory hash map for known attachment thumbnail or cached full file
        if cached_hash:
            if is_thumbnail:
                thumb_path = os.path.join(thumb_dir, f"{cached_hash}.webp")
                if os.path.exists(thumb_path):
                    resp = make_response(send_file(thumb_path, mimetype='image/webp', as_attachment=False))
                    resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
                    resp.headers['ETag'] = f'"{cached_hash}"'
                    return resp
            else:
                full_cached_path = os.path.join(cache_dir, f"{cached_hash}.bin")
                if os.path.exists(full_cached_path):
                    mimetype, _ = mimetypes.guess_type(filename)
                    resp = make_response(send_file(full_cached_path, mimetype=mimetype or 'application/octet-stream', as_attachment=False))
                    resp.headers['Cache-Control'] = 'private, max-age=86400'
                    resp.headers['ETag'] = f'"{cached_hash}"'
                    return resp

        # 3. Download attachment data from Graph
        attachment_data = graph_connector.get_attachment_sync(
            user_email=user_email,
            message_id=message_id,
            attachment_id=attachment_id
        )

        if not attachment_data:
            logger.error(f"Failed to download attachment data | User: {user_email} | Msg: {message_id[:15]}... | Att: {attachment_id[:15]}...")
            return jsonify({'error': 'Attachment not found or failed to download'}), 404

        # Priority: 1. Query parameter, 2. Guess from filename, 3. Default
        mimetype = request.args.get('content_type')
        if mimetype:
            mimetype = mimetype.split('@')[0].strip()
        if not mimetype or '/' not in mimetype:
            mimetype, _ = mimetypes.guess_type(filename)
        if not mimetype:
            mimetype = 'application/octet-stream'

        # 4. Compute content hash (stripping EXIF/metadata for images)
        is_img = mimetype.startswith('image/') or any(filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'])
        content_hash = compute_image_content_hash(attachment_data, is_image=is_img)
        ATTACHMENT_HASH_MAP[lookup_key] = content_hash

        # Cache full raw attachment on disk with LRU eviction to cap disk usage
        full_cached_path = os.path.join(cache_dir, f"{content_hash}.bin")
        if not os.path.exists(full_cached_path):
            try:
                max_cache_mb = int(os.environ.get('MAX_ATTACHMENT_CACHE_MB', '512'))
                # Evict oldest .bin files if cache dir exceeds the configured limit
                existing_bins = sorted(
                    [os.path.join(cache_dir, f) for f in os.listdir(cache_dir) if f.endswith('.bin')],
                    key=lambda p: os.path.getmtime(p)
                )
                total_mb = sum(os.path.getsize(p) for p in existing_bins) / (1024 * 1024)
                while total_mb > max_cache_mb and existing_bins:
                    oldest = existing_bins.pop(0)
                    try:
                        evicted_size = os.path.getsize(oldest) / (1024 * 1024)
                        os.remove(oldest)
                        total_mb -= evicted_size
                        logger.info(f"Cache eviction: removed {os.path.basename(oldest)} ({evicted_size:.1f} MB)")
                    except OSError:
                        break
                with open(full_cached_path, 'wb') as f:
                    f.write(attachment_data)
            except Exception as write_err:
                logger.warning(f"Failed to cache attachment {content_hash}: {write_err}")

        # Check If-None-Match again after hash computation
        if if_none_match and if_none_match.strip('"') == content_hash:
            return '', 304

        # 5. Handle Thumbnail request with deduplicated storage
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

        # 6. Full Attachment Stream
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
