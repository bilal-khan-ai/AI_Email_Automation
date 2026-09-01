# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - Ticket Management Blueprint - start
"""
routes/ticket_routes.py

Ticket lifecycle HTTP routes:
- Ticket list & details retrieval (/api/tickets, /api/get_ticket/<ticket_id>)
- Ticket messages thread retrieval (/api/ticket_messages/<ticket_id>)
- Ticket editing, self-assignment, status toggling (/api/update_ticket, /api/take_ticket, /api/toggle_ticket_status)
- Customer email response delivery (/api/send_to_customer)
- Search tickets (/api/search)
- Synchronous AI draft regeneration (/api/regenerate_ai_sync)
"""

import uuid
import base64
import logging
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import Blueprint, jsonify, request, session
from bs4 import BeautifulSoup

import auth
from auth import login_required, get_current_user
from config import Config
from dashboard_context import (
    sql_logger, graph_connector, user_locks, normalize_legacy_status,
    calculate_stats, get_ai_agent, get_experience_db, get_documentation_db,
    get_socketio, extract_inline_images
)

logger = logging.getLogger(__name__)

ticket_bp = Blueprint('tickets', __name__)


@ticket_bp.route('/api/tickets')
@login_required
def get_tickets():
    """Get tickets with normalized statuses, filtered for staff if applicable."""
    user = get_current_user()
    
    # We fetch all active tickets for everyone, trusting the UI to filter by default 
    # to "My Tickets" for staff users.
    tickets = sql_logger.get_active_tickets()

    # Normalize statuses and add lock information
    for ticket in tickets:
        ticket['status'] = normalize_legacy_status(ticket.get('status'))
        ticket_id = ticket.get('ticket_id')
        if ticket_id in user_locks:
            ticket['locked_by'] = user_locks[ticket_id]['user_id']
            ticket['locked_at'] = user_locks[ticket_id]['locked_at']
        else:
            ticket['locked_by'] = None

    # Calculate statistics
    stats = calculate_stats(tickets)

    return jsonify({
        'tickets': tickets,
        'stats': stats
    })


@ticket_bp.route('/api/get_ticket/<ticket_id>')
@login_required
def get_ticket_details(ticket_id):
    """Get full details for a specific ticket."""
    ticket = sql_logger.get_ticket_by_id(ticket_id)
    if ticket:
        ticket['status'] = normalize_legacy_status(ticket.get('status'))
        if ticket_id in user_locks:
            ticket['locked_by'] = user_locks[ticket_id]['user_id']
            ticket['locked_at'] = user_locks[ticket_id]['locked_at']
        else:
            ticket['locked_by'] = None
        return jsonify(ticket)
    return jsonify({'error': 'Ticket not found'}), 404


@ticket_bp.route('/api/ticket_messages/<ticket_id>')
@login_required
def get_ticket_messages(ticket_id):
    """Get all messages for a specific ticket thread."""
    messages = sql_logger.get_thread_messages(ticket_id)
    
    # Mark ticket as read when thread is viewed
    was_unread = sql_logger.mark_ticket_as_read(ticket_id)
    if was_unread:
        sio = get_socketio()
        if sio:
            sio.emit('ticket_updated', {
                'ticket_id': ticket_id,
                'updated_by': session.get('user_id')
            })
        
    return jsonify({'messages': messages})


@ticket_bp.route('/api/update_ticket', methods=['POST'])
@login_required
def update_ticket():
    """
    Update a ticket's AI draft and assignment.
    IMPORTANT: This does NOT change ticket status. Status is only changed via /api/toggle_ticket_status.
    """
    data = request.json or {}
    ticket_db_id = data.get('row_number')  # Primary key id
    ticket_id = data.get('ticket_id')
    user_id = session.get('user_id')
    
    if not ticket_db_id:
        return jsonify({'error': 'Row number required'}), 400

    # Check if ticket is locked by another user
    if ticket_id in user_locks and user_locks[ticket_id]['user_id'] != user_id:
        return jsonify({
            'error': 'Ticket is being edited by another user',
            'locked_by': user_locks[ticket_id]['user_id']
        }), 423

    # Prepare update payload
    update_payload = {
        'ai_draft': data.get('ai_response'),
        'last_updated': datetime.now().isoformat()
    }
    
    actor_username = session.get('user', {}).get('username', 'system')
    target_assignee = data.get('assigned_to') or actor_username
    update_payload['assigned_to'] = target_assignee

    success = sql_logger.update_ticket_fields(ticket_db_id, update_payload, actor=actor_username)
    
    if success:
        sio = get_socketio()
        if sio:
            sio.emit('ticket_updated', {
                'ticket_id': ticket_id,
                'row_number': ticket_db_id,
                'updated_by': user_id
            })
        
        return jsonify({'success': True, 'message': 'Ticket updated successfully'})
    else:
        return jsonify({
            'error': 'Failed to save ticket',
            'details': f'No ticket found with id={ticket_db_id}'
        }), 500


@ticket_bp.route('/api/take_ticket', methods=['POST'])
@login_required
def take_ticket():
    """Manually take a ticket (assign to self)."""
    data = request.json or {}
    ticket_db_id = data.get('row_number')
    ticket_id = data.get('ticket_id')
    actor_username = session.get('user', {}).get('username', 'system')
    target_assignee = data.get('assigned_to', actor_username)
    
    if not ticket_db_id or not ticket_id:
        return jsonify({'error': 'Missing ticket identifiers'}), 400
        
    update_payload = {
        'assigned_to': target_assignee,
        'last_updated': datetime.now().isoformat()
    }
    
    success = sql_logger.update_ticket_fields(ticket_db_id, update_payload, actor=actor_username)
    
    if success:
        sio = get_socketio()
        if sio:
            sio.emit('ticket_updated', {
                'ticket_id': ticket_id,
                'row_number': ticket_db_id,
                'updated_by': session.get('user_id')
            })
        return jsonify({'success': True, 'assigned_to': actor_username})
    else:
        return jsonify({'error': 'Failed to take ticket'}), 500


@ticket_bp.route('/api/toggle_ticket_status', methods=['POST'])
@login_required
def toggle_ticket_status():
    """
    Toggle or explicitly set ticket status.
    This is the ONLY endpoint that changes ticket status.
    """
    data = request.json or {}
    ticket_db_id = data.get('row_number')
    ticket_id = data.get('ticket_id')
    new_status = data.get('status')
    user_id = session.get('user_id')
    
    if not ticket_db_id:
        return jsonify({'error': 'Row number required'}), 400

    ticket = sql_logger.get_ticket_by_id(ticket_id)
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404

    if not new_status:
        current_status = normalize_legacy_status(ticket.get('status'))
        new_status = 'Closed' if current_status == 'Open' else 'Open'

    if new_status not in ['Open', 'Review', 'Ignore', 'Closed']:
        return jsonify({'error': f'Invalid status: {new_status}'}), 400

    update_data = {
        'status': new_status,
        'last_updated': datetime.now().isoformat()
    }
    
    if new_status == 'Closed':
        update_data['reopened'] = False

    success = sql_logger.update_ticket_fields(ticket_db_id, update_data)

    if success:
        display_id = sql_logger.get_display_id(ticket_id)
        is_resolved = (new_status == 'Closed')
        exp_db = get_experience_db()
        if exp_db is not None:
            exp_db.update_ticket_status(ticket_id, is_resolved, display_id=display_id)
            exp_db.update_authority_status(ticket_id, is_resolved, display_id=display_id)
        sql_logger.mark_ticket_as_authority(ticket_id, is_resolved)

        sio = get_socketio()
        if new_status == 'Closed' and ticket_id in user_locks:
            del user_locks[ticket_id]
            if sio:
                sio.emit('ticket_unlocked', {'ticket_id': ticket_id})

        if sio:
            sio.emit('ticket_status_toggled', {
                'ticket_id': ticket_id,
                'row_number': ticket_db_id,
                'new_status': new_status,
                'updated_by': user_id
            })
        return jsonify({
            'success': True,
            'message': f'Ticket status changed to {new_status}',
            'new_status': new_status
        })
    else:
        return jsonify({
            'error': 'Failed to update ticket status',
            'details': f'No ticket found with id={ticket_db_id}'
        }), 500


@ticket_bp.route('/api/send_to_customer', methods=['POST'])
@login_required
def send_to_customer():
    """Send email response to customer and record sent message in ticket history."""
    try:
        ticket_id = request.form.get('ticket_id')
        ai_response = request.form.get('ai_response')
        assigned_to = request.form.get('assigned_to')
        cc = request.form.get('cc', '')
        bcc = request.form.get('bcc', '')
        send_to = request.form.get('send_to')

        if not ticket_id or not ai_response:
            return jsonify({'error': 'Missing required fields'}), 400

        if not Config.TEST_MODE and not send_to:
            return jsonify({'error': 'Recipient email missing'}), 400

        ticket = sql_logger.get_ticket_by_id(ticket_id)
        if not ticket:
            return jsonify({'error': 'Ticket not found'}), 404

        # Parse regular uploaded attachments
        attachments = []
        attachment_count = int(request.form.get('attachment_count', 0))

        for i in range(attachment_count):
            file = request.files.get(f'attachment_{i}')
            if file and file.filename:
                attachments.append({
                    'name': secure_filename(file.filename),
                    'content': base64.b64encode(file.read()).decode('utf-8'),
                    'contentType': file.content_type or 'application/octet-stream'
                })

        # Parse and extract copy-pasted inline images
        ai_response, inline_imgs = extract_inline_images(ai_response)
        attachments.extend(inline_imgs)

        # Determine actual recipients
        if Config.TEST_MODE:
            final_recipient = Config.TEST_EMAIL
            final_cc = Config.TEST_CC if Config.TEST_CC else ''
            final_bcc = ''
        else:
            final_recipient = send_to
            final_cc = cc
            final_bcc = bcc
        
        messages = sql_logger.get_thread_messages(ticket_id)
        last_message_id = messages[-1].get('message_id') if messages else None

        if Config.TEST_MODE:
            intended_msg = f"<hr><p style='color: #666; font-size: 0.8em;'><b>[TEST MODE]</b><br>"
            intended_msg += f"<b>Intended To:</b> {send_to}<br>"
            if cc: intended_msg += f"<b>Intended CC:</b> {cc}<br>"
            if bcc: intended_msg += f"<b>Intended BCC:</b> {bcc}<br>"
            intended_msg += "</p>"
            ai_response += intended_msg

        if last_message_id:
            logger.info(f"📤 Replying to last message {last_message_id[:10]}... for ticket {ticket_id}")
            success = graph_connector.reply_to_email(
                user_email=Config.USER_EMAIL,
                parent_message_id=last_message_id,
                to_email=final_recipient,
                body=ai_response,
                cc=final_cc,
                bcc=final_bcc,
                attachments=attachments
            )
        else:
            logger.warning(f"⚠️ No messages found for ticket {ticket_id}. Falling back to standard send_email.")
            final_subject = f"Re: {ticket['subject']}"
            if Config.TEST_MODE:
                final_subject = f"{Config.TEST_SUBJECT_TAG}{final_subject}"

            success = graph_connector.send_email(
                user_email=Config.USER_EMAIL,
                recipient=final_recipient,
                subject=final_subject,
                body=ai_response,
                conversation_id=ticket.get('conversation_id'),
                cc=final_cc,
                bcc=final_bcc,
                attachments=attachments
            )

        if not success:
            return jsonify({'error': 'Failed to send email via Graph API'}), 500

        actor_username = session.get('user', {}).get('username', 'system')
        update_payload = {
            'ai_draft': ai_response,
            'last_updated': datetime.now().isoformat(),
            'assigned_to': actor_username
        }

        sql_logger.update_ticket_fields(ticket['id'], update_payload, actor=actor_username)
        sql_logger.log_response_sent(ticket_id, actor_username, final_recipient)

        # Log sent message to thread
        sent_email_id = f"sent_{uuid.uuid4().hex}"
        soup_text = BeautifulSoup(ai_response, 'html.parser').get_text()
        
        formatted_attachments = []
        for idx, att in enumerate(attachments):
            formatted_attachments.append({
                'id': f"att_sent_{uuid.uuid4().hex[:12]}",
                'name': att.get('name'),
                'size': len(att.get('content', '')) * 3 // 4,
                'content_type': att.get('contentType'),
                'content_id': att.get('content_id')
            })

        sent_msg = {
            'id': sent_email_id,
            'internet_message_id': None,
            'sender': Config.USER_EMAIL,
            'to': final_recipient,
            'body_text': soup_text,
            'body_html': ai_response,
            'received': datetime.now(),
            'attachments': formatted_attachments,
            'cc': final_cc,
            'bcc': final_bcc
        }
        
        sql_logger.log_message(ticket_id, sent_msg, is_internal=True)

        sio = get_socketio()
        if sio:
            sio.emit('email_sent', {'ticket_id': ticket_id})

        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"Error sending email: {e}")
        return jsonify({'error': str(e)}), 500


@ticket_bp.route('/api/search', methods=['POST'])
@login_required
def search_tickets():
    """Search tickets by keyword in subjects, sender, and customer email."""
    query = (request.json or {}).get('query', '')
    if not query:
        return jsonify({'tickets': []})
    
    tickets = sql_logger.search_tickets(query)
    for ticket in tickets:
        ticket['status'] = normalize_legacy_status(ticket.get('status'))
    
    return jsonify({'tickets': tickets})


@ticket_bp.route('/api/regenerate_ai', methods=['POST'])
@login_required
def regenerate_ai():
    """DEPRECATED: Use /api/regenerate_ai_sync instead for low-latency updates."""
    return jsonify({'success': False, 'error': 'This endpoint is deprecated. Please refresh your dashboard.'}), 410


@ticket_bp.route('/api/regenerate_ai_sync', methods=['POST'])
@login_required
def regenerate_ai_sync():
    """Synchronously regenerate AI draft with optional user instructions and RAG context."""
    try:
        data = request.json or {}
        ticket_id = data.get('ticket_id')
        user_instructions = data.get('user_instructions')
        current_draft = data.get('current_draft')
        
        if not ticket_id:
            return jsonify({'success': False, 'error': 'No ticket ID provided'}), 400
            
        messages = sql_logger.get_thread_messages(ticket_id)
        if not messages:
            return jsonify({'success': False, 'error': 'No messages found for ticket'}), 404
            
        last_customer_msg = next((m for m in reversed(messages) if not m.get('is_internal')), messages[-1])
        query = last_customer_msg.get('body_text', '')
        
        docs = []
        experiences = []
        if Config.ENABLE_RAG:
            doc_db = get_documentation_db()
            if doc_db:
                docs_raw = doc_db.query_bookstack(query, n_results=Config.TOP_K_RESULTS)
                if docs_raw and 'documents' in docs_raw and docs_raw['documents']:
                    for i, doc_text in enumerate(docs_raw['documents'][0]):
                        docs.append({
                            'content': doc_text,
                            'metadata': docs_raw['metadatas'][0][i] if 'metadatas' in docs_raw else {},
                            'distance': docs_raw['distances'][0][i] if 'distances' in docs_raw else 0.5
                        })

            exp_db = get_experience_db()
            if exp_db:
                experiences = exp_db.search_similar(query, top_k=Config.TOP_K_RESULTS)
        else:
            logger.info("⏭️ Skipping RAG search in dashboard regen (ENABLE_RAG=False)")
        
        agent = get_ai_agent()
        if not agent:
            return jsonify({'success': False, 'error': 'AI agent is not configured or disabled'}), 503

        draft, provenance = agent.generate_response(
            messages=messages,
            documentation_context=docs,
            experience_context=experiences,
            user_instructions=user_instructions,
            current_draft=current_draft,
            customer_email=messages[0].get('sender'),
            subject=messages[0].get('subject')
        )
        
        if not draft:
            return jsonify({'success': False, 'error': 'AI failed to generate a response'}), 500
            
        update_payload = {
            'ai_draft': draft,
            'last_updated': datetime.now().isoformat()
        }
        
        ticket = sql_logger.get_ticket_by_id(ticket_id)
        if ticket:
            actor = session.get('user', {}).get('username', 'system')
            sql_logger.update_ticket_fields(ticket['id'], update_payload, actor=actor)
            
        sio = get_socketio()
        if sio:
            sio.emit('ticket_updated', {
                'ticket_id': ticket_id,
                'updated_by': session.get('user_id')
            })
        
        return jsonify({
            'success': True, 
            'draft': draft,
            'provenance': provenance
        })
        
    except Exception as e:
        logger.error(f"Error in sync regeneration: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

__all__ = ['ticket_bp']
# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - Ticket Management Blueprint - end
