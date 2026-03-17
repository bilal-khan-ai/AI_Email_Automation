from flask import Blueprint, jsonify, request, session
from api.models.sql_logger import SQLLogger
from api.auth import login_required, get_current_user
from config import Config
from datetime import datetime

tickets_bp = Blueprint('tickets', __name__)
sql_logger = SQLLogger("postgres")
sql_logger.authenticate()

def normalize_legacy_status(status: str) -> str:
    if not status:
        return 'Open'
    status_lower = status.lower()
    if status_lower in ['resolved', 'completed', 'closed']:
        return 'Closed'
    return 'Open'

def calculate_stats(tickets):
    total = len(tickets)
    open_count = sum(1 for t in tickets if normalize_legacy_status(t.get('status')) == 'Open')
    return {
        'total': total,
        'open': open_count,
        'closed': total - open_count,
        'last_updated': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

@tickets_bp.route('/api/tickets')
@login_required
def get_tickets():
    tickets = sql_logger.get_active_tickets()
    for ticket in tickets:
        ticket['status'] = normalize_legacy_status(ticket.get('status'))
    stats = calculate_stats(tickets)
    return jsonify({'tickets': tickets, 'stats': stats})

@tickets_bp.route('/api/get_ticket/<ticket_id>')
@login_required
def get_ticket_details(ticket_id):
    ticket = sql_logger.get_ticket_by_id(ticket_id)
    if ticket:
        ticket['status'] = normalize_legacy_status(ticket.get('status'))
        return jsonify(ticket)
    return jsonify({'error': 'Ticket not found'}), 404

@tickets_bp.route('/api/ticket_messages/<ticket_id>')
@login_required
def get_ticket_messages(ticket_id):
    messages = sql_logger.get_thread_messages(ticket_id)
    return jsonify({'messages': messages})

@tickets_bp.route('/api/update_ticket', methods=['POST'])
@login_required
def update_ticket():
    data = request.json
    ticket_db_id = data.get('row_number')
    if not ticket_db_id:
        return jsonify({'error': 'Row number required'}), 400
    
    update_payload = {
        'ai_draft': data.get('ai_response'),
        'last_updated': datetime.now().isoformat()
    }
    if 'assigned_to' in data:
        update_payload['assigned_to'] = data.get('assigned_to')
        
    if sql_logger.update_ticket_fields(ticket_db_id, update_payload):
        return jsonify({'success': True})
    return jsonify({'error': 'Update failed'}), 500

@tickets_bp.route('/api/toggle_ticket_status', methods=['POST'])
@login_required
def toggle_status():
    data = request.json
    ticket_id = data.get('ticket_id')
    ticket_db_id = data.get('row_number')
    
    ticket = sql_logger.get_ticket_by_id(ticket_id)
    if not ticket: return jsonify({'error': 'Not found'}), 404
    
    current = normalize_legacy_status(ticket.get('status'))
    new_status = 'Closed' if current == 'Open' else 'Open'
    
    if sql_logger.update_ticket_fields(ticket_db_id, {'status': new_status}):
        return jsonify({'success': True, 'new_status': new_status})
    return jsonify({'error': 'Toggle failed'}), 500
