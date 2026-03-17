from flask import Blueprint, jsonify, request, session, send_file
import io
import base64
from werkzeug.utils import secure_filename
from api.services.graph_connector import GraphConnector
from api.auth import login_required
from config import Config

actions_bp = Blueprint('actions', __name__)

# Initialize GraphConnector
graph_connector = GraphConnector(
    Config.AZURE_CLIENT_ID,
    Config.AZURE_CLIENT_SECRET,
    Config.AZURE_TENANT_ID
)
graph_connector.authenticate()

@actions_bp.route('/api/send_to_customer', methods=['POST'])
@login_required
def send_to_customer():
    try:
        data = request.form
        ticket_id = data.get('ticket_id')
        ai_response = data.get('ai_response')
        send_to = data.get('send_to')
        
        if not ticket_id or not ai_response:
            return jsonify({'error': 'Missing required fields'}), 400

        attachments = []
        # Support multiple file uploads
        files = request.files.getlist('attachments')
        for file in files:
            if file.filename:
                attachments.append({
                    'name': secure_filename(file.filename),
                    'content': base64.b64encode(file.read()).decode('utf-8'),
                    'contentType': file.content_type or 'application/octet-stream'
                })

        success = graph_connector.send_email(
            user_email=Config.USER_EMAIL,
            recipient=Config.TEST_EMAIL if Config.TEST_MODE else send_to,
            subject=f"Re: Support Update", # In real app, we'd fetch original subject
            body=ai_response,
            attachments=attachments
        )

        if success:
            return jsonify({'success': True})
        return jsonify({'error': 'Failed to send email'}), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@actions_bp.route('/api/download_attachment')
@login_required
def download_attachment():
    try:
        msg_id = request.args.get('message_id')
        att_id = request.args.get('attachment_id')
        name = request.args.get('name', 'attachment')

        data = graph_connector.get_attachment_sync(Config.USER_EMAIL, msg_id, att_id)
        if not data:
            return jsonify({'error': 'Not found'}), 404

        return send_file(
            io.BytesIO(data),
            as_attachment=True,
            download_name=secure_filename(name),
            mimetype="application/octet-stream"
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500
