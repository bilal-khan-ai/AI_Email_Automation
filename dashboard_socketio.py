import os
import io
import time
import logging
import secrets
import threading
import base64
from datetime import datetime, date
from werkzeug.utils import secure_filename
from flask import Flask, render_template, jsonify, request, send_file, session, redirect, url_for, flash
from flask_socketio import SocketIO, emit, join_room, leave_room
from modules.sql_logger import SQLLogger
from modules.env_manager import EnvManager
from modules.graph_connector import GraphConnector
import auth
from auth import init_auth, login_required, socketio_login_required, socketio_admin_required, admin_required, get_current_user, is_admin
from config import Config
from modules.openai_agent import OpenAIAgent
from modules.vector_db import VectorDatabase, BookVectorDB

from flask.json.provider import DefaultJSONProvider

class CustomJSONProvider(DefaultJSONProvider):
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)

app = Flask(__name__, template_folder='Pages')
app.json = CustomJSONProvider(app)
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# UPDATED: Initialize custom authentication
app.config['SECRET_KEY'] = Config.SECRET_KEY
init_auth(app)  # Initialize custom authentication system

# Initialize SocketIO with async mode and custom JSON provider
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='threading',
    json=app.json,
    logger=True,
    engineio_logger=True
)

# Ensure data directory exists
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

# Initialize SQL logger (db_file argument is ignored by PostgreSQL backend)
sql_logger = SQLLogger("postgres")
sql_logger.authenticate()
env_manager = EnvManager()

# ============================================================================
# CRITICAL FIX: Initialize GraphConnector ONCE globally at startup
# This prevents fresh authentication on every attachment download/email send
# ============================================================================
print("Initializing global Graph connector...")
graph_connector = GraphConnector(
    Config.AZURE_CLIENT_ID,
    Config.AZURE_CLIENT_SECRET,
    Config.AZURE_TENANT_ID
)

if not graph_connector.authenticate():
    print("WARNING: Failed to authenticate with Microsoft Graph at startup")
    print("    Attachment downloads and email sending may fail")
else:
    print("SUCCESS: Global Graph connector authenticated successfully")

# Create uploads directory for temporary file storage
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Initialize AI and Vector DBs for synchronous tasks
ai_agent = OpenAIAgent(Config.OPENAI_API_KEY)
ai_agent.authenticate()

# Lazy initialization helper functions to prevent Gunicorn process forks from deadlocking on SQLite database files
_experience_db = None
_documentation_db = None

def get_experience_db():
    global _experience_db
    if _experience_db is None:
        print("Initializing lazy experience vector DB...")
        from modules.vector_db import VectorDatabase
        _experience_db = VectorDatabase(Config.CHROMA_DB_PATH, Config.COLLECTION_NAME, force_cpu=True)
    return _experience_db

def get_documentation_db():
    global _documentation_db
    if _documentation_db is None:
        print("Initializing lazy documentation vector DB...")
        from modules.vector_db import BookVectorDB
        _documentation_db = BookVectorDB(Config.BOOKSTACK_DB_PATH)
    return _documentation_db


# Track connected users and their rooms
connected_users = {}
user_locks = {}  # Track which tickets are being edited by which users


def normalize_legacy_status(status: str) -> str:
    """
    Map legacy status values to Open/Review/Ignore/Closed semantics.
    
    Args:
        status: Legacy status value
        
    Returns:
        Normalized status ('Open', 'Review', 'Ignore', or 'Closed')
    """
    if not status:
        return 'Open'
    
    status_lower = status.lower()
    
    # Map legacy statuses
    if status_lower in ['resolved', 'completed', 'closed']:
        return 'Closed'
    elif status_lower in ['review', 'pending review']:
        return 'Review'
    elif status_lower in ['ignore']:
        return 'Ignore'
    elif status_lower in ['pending', 'open', 'in progress']:
        return 'Open'
    else:
        # Default unknown statuses to Open
        return 'Open'


def calculate_stats(tickets, user=None):
    """
    Calculate ticket statistics with Open/Review/Ignore/Closed semantics.
    If user is staff, stats will naturally be filtered by the tickets passed in.
    """
    open_count = sum(1 for t in tickets if normalize_legacy_status(t.get('status')) == 'Open')
    review_count = sum(1 for t in tickets if normalize_legacy_status(t.get('status')) == 'Review')
    ignore_count = sum(1 for t in tickets if normalize_legacy_status(t.get('status')) == 'Ignore')
    closed_count = sum(1 for t in tickets if normalize_legacy_status(t.get('status')) == 'Closed')
    total = open_count + review_count + closed_count # Ignore tickets are excluded from total

    return {
        'total': total,
        'open': open_count,
        'review': review_count,
        'ignore': ignore_count,
        'closed': closed_count,
        'last_updated': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


# Background thread to broadcast updates
def background_thread():
    """Background thread to periodically broadcast ticket updates to all connected clients"""
    while True:
        time.sleep(5)  # Check every 5 seconds
        try:
            # Stats update for all is now problematic because stats are role-based.
            # We skip global stats update broadcast and let individual clients refresh or use specific events.
            pass
        except Exception as e:
            print(f"Error in background thread: {e}")


# Start background thread
thread = threading.Thread(target=background_thread)
thread.daemon = True
thread.start()


# ============================================================================
# AUTHENTICATION ROUTES - Custom Username/Password Auth
# ============================================================================

# Silent route for browser-specific requests to reduce log noise
@app.route('/.well-known/appspecific/com.chrome.devtools.json')
def silent_404():
    return jsonify({'error': 'Not Found'}), 404


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page with username/password authentication"""
    # If already logged in, redirect to dashboard
    if 'user' in session:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            flash('Please enter both username and password', 'danger')
            return render_template('login.html')
        
        # Authenticate user
        user = auth.user_manager.authenticate_user(username, password)
        
        if user:
            # Store user in session
            session['user'] = user
            session.permanent = True
            
            # Generate unique user ID for this session (used for locking)
            session['user_id'] = f"{username}_{secrets.token_hex(4)}"
            
            flash(f'Welcome back, {username}!', 'success')
            
            # Redirect based on role
            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)
            
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password', 'danger')
            return render_template('login.html')
    
    return render_template('login.html')


@app.route('/logout')
def logout():
    """Logout user"""
    username = session.get('user', {}).get('username', 'User')
    session.clear()
    flash(f'Goodbye, {username}!', 'success')
    return redirect(url_for('login'))


# ============================================================================
@app.route('/management')
@admin_required
def management():
    """Admin management page"""
    users = auth.user_manager.list_users()
    # Filter only users who are marked as assignable (Staff or Admin)
    staff_members = [u for u in users if u['role'] in ['staff', 'admin']]
    staff_usernames = [u['username'] for u in staff_members]
    
    # Get stats for all tickets for admin overview
    all_tickets = sql_logger.get_active_tickets()
    global_stats = calculate_stats(all_tickets)
    
    return render_template('management.html', 
                          user=get_current_user(), 
                          users=users, 
                          staff_members=staff_members,
                          global_stats=global_stats)


@app.route('/admin/users/toggle-status/<username>', methods=['POST'])
@admin_required
def admin_toggle_user_status(username):
    """Enable/Disable a user (admin only)"""
    if username == session.get('user', {}).get('username'):
        flash('❌ You cannot disable your own account', 'danger')
        return redirect(url_for('management'))
    
    if auth.user_manager.toggle_user_status(username):
        user = auth.user_manager.get_user(username)
        status = "enabled" if user['is_active'] else "disabled"
        flash(f'✅ Login access for "{username}" has been {status}', 'success')
    else:
        flash(f'❌ Failed to toggle account status for user "{username}"', 'danger')
    
    return redirect(url_for('management'))


@app.route('/admin/users/toggle-assignable/<username>', methods=['POST'])
@admin_required
def admin_toggle_user_assignment(username):
    """Enable/Disable ticket assignment for a user (admin only)"""
    # Admins ARE allowed to toggle their own assignment status
    if auth.user_manager.toggle_assignable_status(username):
        user = auth.user_manager.get_user(username)
        status = "enabled" if user['is_assignable'] else "disabled"
        flash(f'✅ Ticket assignment for "{username}" has been {status}', 'success')
    else:
        flash(f'❌ Failed to toggle assignment status for user "{username}"', 'danger')
    
    return redirect(url_for('management'))


@app.route('/admin/users/create', methods=['POST'])
@admin_required
def admin_create_user():
    """Create a new user (admin only)"""
    username = request.form.get('username')
    password = request.form.get('password')
    role = request.form.get('role', 'staff')
    
    if not username or not password:
        flash('Username and password are required', 'danger')
        return redirect(url_for('management'))
    
    if auth.user_manager.create_user(username, password, role):
        flash(f'✅ User "{username}" created successfully (Role: {role})', 'success')
    else:
        flash(f'❌ Failed to create user "{username}" (username may already exist)', 'danger')
    
    return redirect(url_for('management'))


@app.route('/admin/users/<username>/delete', methods=['POST'])
@admin_required
def admin_delete_user(username):
    """Delete a user (admin only)"""
    # Prevent deleting yourself
    if username == session.get('user', {}).get('username'):
        flash('❌ You cannot delete your own account', 'danger')
        return redirect(url_for('management'))
    
    if auth.user_manager.delete_user(username):
        flash(f'✅ User "{username}" has been deleted', 'success')
    else:
        flash(f'❌ Failed to delete user "{username}"', 'danger')
    
    return redirect(url_for('management'))


@app.route('/admin/users/<username>/change-password', methods=['POST'])
@admin_required
def admin_change_password(username):
    """Change user password (admin only, restrictions apply)"""
    new_password = request.form.get('new_password')
    current_user = get_current_user()
    target_user = auth.user_manager.get_user(username)
    
    if not new_password:
        flash('New password is required', 'danger')
        return redirect(url_for('management'))

    # BLOCK: Admin to Admin password changing (unless it's yourself)
    if target_user and target_user['role'] == 'admin' and username != current_user['username']:
        flash('❌ You do not have permission to change another administrator\'s password', 'danger')
        return redirect(url_for('management'))
    
    if auth.user_manager.update_password(username, new_password):
        flash(f'✅ Password changed for user "{username}"', 'success')
    else:
        flash(f'❌ Failed to change password for user "{username}"', 'danger')
    
    return redirect(url_for('management'))


@app.route('/api/user/change-password', methods=['POST'])
@login_required
def user_change_password():
    """Route for any logged-in user to change their own password"""
    new_password = request.form.get('new_password')
    current_user = get_current_user()
    
    if not new_password:
        return jsonify({'error': 'New password is required'}), 400
    
    if len(new_password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters long'}), 400

    if auth.user_manager.update_password(current_user['username'], new_password):
        return jsonify({'success': True, 'message': 'Password updated successfully'})
    else:
        return jsonify({'error': 'Failed to update password'}), 500
# ============================================================================
# DASHBOARD ROUTES - Protected by Authentication
# ============================================================================

@app.route('/')
@login_required
def index():
    """Main dashboard page - requires authentication"""
    user = get_current_user()
    
    # Redirect admins to management by default, unless they explicitly want the dashboard view
    # if user['role'] == 'admin' and request.args.get('view') != 'staff':
    #     return redirect(url_for('management'))
    
    # Get only active assignable staff/admins for assignment dropdown
    users = auth.user_manager.list_users()
    staff_members = [u for u in users if u.get('is_assignable', True) and u['role'] in ['staff', 'admin']]
    
    # Pass whether the user is an admin to the template
    return render_template('dashboard.html', 
                          user=user, 
                          staff_members=staff_members, 
                          is_admin=(user['role'] == 'admin'),
                          test_mode=Config.TEST_MODE,
                          test_email=Config.TEST_EMAIL,
                          test_cc=Config.TEST_CC)


@app.route('/api/tickets')
@login_required
def get_tickets():
    """Get tickets with normalized statuses, filtered for staff if applicable"""
    user = get_current_user()
    
    # We now fetch all active tickets for everyone, trusting the UI to filter by default 
    # to "My Tickets" for staff users.
    tickets = sql_logger.get_active_tickets()

    # Normalize statuses and add lock information
    for ticket in tickets:
        # Normalize status
        ticket['status'] = normalize_legacy_status(ticket.get('status'))
        
        # Add lock information
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


@app.route('/api/get_ticket/<ticket_id>')
@login_required
def get_ticket_details(ticket_id):
    """Get full details for a specific ticket"""
    ticket = sql_logger.get_ticket_by_id(ticket_id)
    if ticket:
        # Normalize status
        ticket['status'] = normalize_legacy_status(ticket.get('status'))
        
        # Add lock information
        if ticket_id in user_locks:
            ticket['locked_by'] = user_locks[ticket_id]['user_id']
            ticket['locked_at'] = user_locks[ticket_id]['locked_at']
        else:
            ticket['locked_by'] = None
        return jsonify(ticket)
    return jsonify({'error': 'Ticket not found'}), 404


@app.route('/api/ticket_messages/<ticket_id>')
@login_required
def get_ticket_messages(ticket_id):
    """Get all messages for a specific ticket thread"""
    messages = sql_logger.get_thread_messages(ticket_id)
    
    # Mark ticket as read when thread is viewed
    was_unread = sql_logger.mark_ticket_as_read(ticket_id)
    if was_unread:
        # Broadcast real-time update to all active dashboards to clear the badge
        socketio.emit('ticket_updated', {
            'ticket_id': ticket_id,
            'updated_by': session.get('user_id')
        })
        
    return jsonify({'messages': messages})


@app.route('/api/update_ticket', methods=['POST'])
@login_required
def update_ticket():
    """
    Update a ticket's AI draft and assignment.
    
    IMPORTANT: This does NOT change ticket status.
    Status is only changed via /api/toggle_ticket_status
    """
    data = request.json
    ticket_db_id = data.get('row_number')  # This is actually the database primary key (id)
    ticket_id = data.get('ticket_id')
    user_id = session.get('user_id')
    
    if not ticket_db_id:
        return jsonify({'error': 'Row number required'}), 400

    # Check if ticket is locked by another user
    if ticket_id in user_locks and user_locks[ticket_id]['user_id'] != user_id:
        return jsonify({
            'error': f'Ticket is being edited by another user',
            'locked_by': user_locks[ticket_id]['user_id']
        }), 423  # 423 Locked status code

    # Prepare the update payload - only draft and assignment
    update_payload = {
        'ai_draft': data.get('ai_response'),
        'last_updated': datetime.now().isoformat()
    }
    
    # Standardize actor identification
    actor_username = session.get('user', {}).get('username', 'system')
    
    # AUTO-ASSIGNMENT: Only assign to the current user if it was previously Unassigned
    # and they didn't manually pick someone else in the dropdown (which is passed in data)
    target_assignee = data.get('assigned_to')
    
    # If no assignee provided, or we want to ensure it's not unassigned when saving
    if not target_assignee:
        target_assignee = actor_username
        
    update_payload['assigned_to'] = target_assignee

    # Perform SQL Update using the database primary key
    success = sql_logger.update_ticket_fields(ticket_db_id, update_payload, actor=actor_username)
    
    if success:
        # Broadcast update to all connected clients
        socketio.emit('ticket_updated', {
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


@app.route('/api/take_ticket', methods=['POST'])
@login_required
def take_ticket():
    """
    Manually take a ticket (assign to self).
    """
    data = request.json
    ticket_db_id = data.get('row_number')
    ticket_id = data.get('ticket_id')
    actor_username = session.get('user', {}).get('username', 'system')
    target_assignee = data.get('assigned_to', actor_username) # Default to self if not specified
    
    if not ticket_db_id or not ticket_id:
        return jsonify({'error': 'Missing ticket identifiers'}), 400
        
    update_payload = {
        'assigned_to': target_assignee,
        'last_updated': datetime.now().isoformat()
    }
    
    success = sql_logger.update_ticket_fields(ticket_db_id, update_payload, actor=actor_username)
    
    if success:
        # Broadcast update
        socketio.emit('ticket_updated', {
            'ticket_id': ticket_id,
            'row_number': ticket_db_id,
            'updated_by': session.get('user_id')
        })
        return jsonify({'success': True, 'assigned_to': actor_username})
    else:
        return jsonify({'error': 'Failed to take ticket'}), 500


@app.route('/api/toggle_ticket_status', methods=['POST'])
@login_required
def toggle_ticket_status():
    """
    Toggle or explicitly set ticket status.
    
    This is the ONLY endpoint that changes ticket status.
    """
    data = request.json
    ticket_db_id = data.get('row_number')  # This is the database primary key (id)
    ticket_id = data.get('ticket_id')
    new_status = data.get('status') # NEW: allow passing custom status
    user_id = session.get('user_id')
    
    if not ticket_db_id:
        return jsonify({'error': 'Row number required'}), 400

    # Get current ticket to determine current status
    ticket = sql_logger.get_ticket_by_id(ticket_id)
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404

    # If no status passed, fallback to binary toggle for legacy behavior
    if not new_status:
        current_status = normalize_legacy_status(ticket.get('status'))
        new_status = 'Closed' if current_status == 'Open' else 'Open'

    # Validate status
    if new_status not in ['Open', 'Review', 'Ignore', 'Closed']:
        return jsonify({'error': f'Invalid status: {new_status}'}), 400

    # Update status
    update_data = {
        'status': new_status,
        'last_updated': datetime.now().isoformat()
    }
    
    # If closing, reset the reopened flag
    if new_status == 'Closed':
        update_data['reopened'] = False

    success = sql_logger.update_ticket_fields(ticket_db_id, update_data)

    if success:
        # Resolve friendly ID for logging
        display_id = sql_logger.get_display_id(ticket_id)
        
        # Update vector DB with resolution status for RAG quality
        is_resolved = (new_status == 'Closed')
        get_experience_db().update_ticket_status(ticket_id, is_resolved, display_id=display_id)
        
        # New: Resolution Gold Labeling (Gold Labeling ensures high-quality RAG retrieval for repeat issues)
        get_experience_db().update_authority_status(ticket_id, is_resolved, display_id=display_id)
        sql_logger.mark_ticket_as_authority(ticket_id, is_resolved)

        # Release lock if closing
        if new_status == 'Closed' and ticket_id in user_locks:
            del user_locks[ticket_id]
            socketio.emit('ticket_unlocked', {'ticket_id': ticket_id})

        # Broadcast status change
        socketio.emit('ticket_status_toggled', {
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


@app.route('/api/send_to_customer', methods=['POST'])
@login_required
def send_to_customer():
    """
    Send email to customer without changing ticket status.
    """
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

        # Get ticket
        ticket = sql_logger.get_ticket_by_id(ticket_id)
        if not ticket:
            return jsonify({'error': 'Ticket not found'}), 404

        # Attachments
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
        from bs4 import BeautifulSoup
        import re
        import uuid

        def extract_inline_images(html_content):
            if not html_content:
                return html_content, []
            
            soup = BeautifulSoup(html_content, 'html.parser')
            inline_attachments = []
            img_tags = soup.find_all('img')
            
            for idx, img in enumerate(img_tags):
                src = img.get('src', '')
                match = re.match(r'^data:(image/[a-zA-Z0-9\-\+\.]+);base64,(.+)$', src)
                if match:
                    content_type = match.group(1)
                    base64_data = match.group(2).strip()
                    
                    # Extract file extension
                    ext = content_type.split('/')[-1]
                    if ext == 'jpeg':
                        ext = 'jpg'
                    
                    cid = f"inline_img_{uuid.uuid4().hex[:12]}"
                    filename = f"inline_image_{idx}.{ext}"
                    
                    img['src'] = f"cid:{cid}"
                    
                    inline_attachments.append({
                        'name': filename,
                        'content': base64_data,
                        'contentType': content_type,
                        'is_inline': True,
                        'content_id': cid
                    })
            return str(soup), inline_attachments

        ai_response, inline_imgs = extract_inline_images(ai_response)
        attachments.extend(inline_imgs)

        # Send email - Try to reply to the last message for threading
        if Config.TEST_MODE:
            final_recipient = Config.TEST_EMAIL
            final_cc = Config.TEST_CC if Config.TEST_CC else ''
            final_bcc = '' # Always clear BCC in test mode
        else:
            final_recipient = send_to
            final_cc = cc
            final_bcc = bcc
        
        # Get thread messages to find the last message ID
        messages = sql_logger.get_thread_messages(ticket_id)
        last_message_id = None
        if messages:
            # The last message ID we received from the Graph API
            last_message_id = messages[-1].get('message_id')

        if Config.TEST_MODE:
            # Inform user in the email body about the intended recipients
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
            # Fallback to standard send if no messages found (should not happen for existing tickets)
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

        # Update ticket (NO row_number garbage)
        actor_username = session.get('user', {}).get('username', 'system')
        update_payload = {
            'ai_draft': ai_response,
            'last_updated': datetime.now().isoformat(),
            'assigned_to': actor_username # AUTO-ASSIGNMENT on send
        }

        sql_logger.update_ticket_fields(ticket['id'], update_payload, actor=actor_username)
        sql_logger.log_response_sent(ticket_id, actor_username, final_recipient)

        # Compile and log sent message immediately into database for real-time thread rendering
        sent_email_id = f"sent_{uuid.uuid4().hex}"
        soup_text = BeautifulSoup(ai_response, 'html.parser').get_text()
        
        formatted_attachments = []
        for idx, att in enumerate(attachments):
            formatted_attachments.append({
                'id': f"att_sent_{uuid.uuid4().hex[:12]}",
                'name': att.get('name'),
                'size': len(att.get('content', '')) * 3 // 4,  # Estimate size from base64 string length
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

        socketio.emit('email_sent', {
            'ticket_id': ticket_id
        })

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error sending email: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/contacts')
@login_required
def get_contacts():
    """Get unique list of all customer and staff emails for autofill."""
    try:
        # 1. Get staff emails
        staff_users = auth.user_manager.list_users()
        staff_emails = [u['username'] for u in staff_users if '@' in u['username']]
        
        # 2. Get customer emails
        customer_emails = sql_logger.get_all_contact_emails()
        
        # 3. Combine and de-duplicate
        all_contacts = list(set(staff_emails + customer_emails))
        
        # 4. Optional: sort for better UI experience
        all_contacts.sort()
        
        return jsonify({'contacts': all_contacts})
    except Exception as e:
        logger.error(f"Error fetching contacts: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/search', methods=['POST'])
@login_required
def search_tickets():
    """Search tickets"""
    query = request.json.get('query', '')
    if not query:
        return jsonify({'tickets': []})
    
    tickets = sql_logger.search_tickets(query)
    
    # Normalize statuses
    for ticket in tickets:
        ticket['status'] = normalize_legacy_status(ticket.get('status'))
    
    return jsonify({'tickets': tickets})


@app.route('/api/view_attachment', methods=['GET'])
@login_required
def view_attachment():
    """
    Serve attachment for inline viewing or preview.
    """
    try:
        user_email = request.args.get('user_email', Config.USER_EMAIL)
        message_id = request.args.get('message_id')
        attachment_id = request.args.get('attachment_id')
        filename = request.args.get('filename', 'attachment')

        if not message_id or not attachment_id:
            return jsonify({'error': 'Missing message_id or attachment_id'}), 400

        attachment_data = graph_connector.get_attachment_sync(
            user_email=user_email,
            message_id=message_id,
            attachment_id=attachment_id
        )

        if not attachment_data:
            return jsonify({'error': 'Attachment not found or failed to download'}), 404

        # Priority: 1. Query parameter, 2. Guess from filename, 3. Default
        mimetype = request.args.get('content_type')
        if not mimetype:
            import mimetypes
            mimetype, _ = mimetypes.guess_type(filename)
        
        if not mimetype:
            mimetype = 'application/octet-stream'

        return send_file(
            io.BytesIO(attachment_data),
            mimetype=mimetype,
            as_attachment=False
        )

    except Exception as e:
        print(f"Error viewing attachment: {e}")
        return jsonify({'error': f'Failed to view attachment: {str(e)}'}), 500



@app.route('/api/config', methods=['GET'])
@login_required
def get_config():
    """
    Expose dynamic client-side configuration parameters to the frontend dashboard.
    """
    try:
        return jsonify({
            'INTERNAL_NOTE_AS_RESPONSE': Config.INTERNAL_NOTE_AS_RESPONSE,
            'USER_EMAIL': Config.USER_EMAIL,
            'TEST_MODE': Config.TEST_MODE
        })
    except Exception as e:
        logger.error(f"Error serving config: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/download_attachment', methods=['GET'])
@login_required
def download_attachment():
    """
    Download attachment from Microsoft Graph.
    """
    try:
        user_email = request.args.get('user_email', Config.USER_EMAIL)
        message_id = request.args.get('message_id')
        attachment_id = request.args.get('attachment_id')
        attachment_name = request.args.get('attachment_name', 'attachment')

        if not message_id or not attachment_id:
            return jsonify({'error': 'Missing message_id or attachment_id'}), 400

        attachment_data = graph_connector.get_attachment_sync(
            user_email=user_email,
            message_id=message_id,
            attachment_id=attachment_id
        )

        if not attachment_data:
            return jsonify({'error': 'Attachment failed to download'}), 404

        import mimetypes
        mimetype, _ = mimetypes.guess_type(attachment_name)
        if not mimetype:
            mimetype = 'application/octet-stream'

        return send_file(
            io.BytesIO(attachment_data),
            as_attachment=True,
            download_name=secure_filename(attachment_name),
            mimetype=mimetype
        )

    except Exception as e:
        print(f"Error downloading attachment: {e}")
        return jsonify({'error': f'Failed to download attachment: {str(e)}'}), 500

@app.route('/api/holidays', methods=['GET'])
@login_required
def get_holidays_api():
    try:
        holidays = sql_logger.get_holidays()
        return jsonify({'holidays': holidays})
    except Exception as e:
        logger.error(f"Error fetching holidays: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/holidays', methods=['POST'])
@login_required
def add_holiday_api():
    data = request.json or {}
    date_str = data.get('date')
    holiday_name = data.get('holiday')
    if not date_str or not holiday_name:
        return jsonify({'error': 'Missing date or holiday name'}), 400
    try:
        res = sql_logger.add_holiday(date_str, holiday_name)
        return jsonify({'success': True, 'result': res})
    except ValueError as ve:
        return jsonify({'error': str(ve)}), 400
    except Exception as e:
        logger.error(f"Error adding holiday: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/holidays', methods=['PUT'])
@login_required
def update_holiday_api():
    data = request.json or {}
    old_date = data.get('old_date')
    new_date = data.get('date')
    holiday_name = data.get('holiday')
    if not old_date or not new_date or not holiday_name:
        return jsonify({'error': 'Missing required fields (old_date, date, holiday)'}), 400
    try:
        res = sql_logger.update_holiday(old_date, new_date, holiday_name)
        return jsonify({'success': True, 'result': res})
    except ValueError as ve:
        return jsonify({'error': str(ve)}), 400
    except Exception as e:
        logger.error(f"Error updating holiday: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/holidays', methods=['DELETE'])
@login_required
def delete_holidays_api():
    data = request.json or {}
    dates = data.get('dates', [])
    if not dates:
        return jsonify({'error': 'No dates provided for deletion'}), 400
    try:
        success = sql_logger.delete_holidays(dates)
        if success:
            return jsonify({'success': True})
        return jsonify({'error': 'Failed to delete holidays'}), 500
    except Exception as e:
        logger.error(f"Error deleting holidays: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/client_groups', methods=['GET'])
@login_required
def get_client_groups_api():
    try:
        groups = sql_logger.get_all_client_entities()
        return jsonify({'groups': groups})
    except Exception as e:
        logger.error(f"Error fetching client groups: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/client_groups', methods=['POST'])
@login_required
def create_client_group_api():
    data = request.json or {}
    name = data.get('name')
    domains = data.get('domains')
    if not name or not domains:
        return jsonify({'error': 'Missing name or domains'}), 400
    try:
        success = sql_logger.create_client_group(name, domains)
        if success:
            return jsonify({'success': True})
        return jsonify({'error': 'Failed to create client group'}), 500
    except Exception as e:
        logger.error(f"Error creating client group: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/client_groups/merge', methods=['POST'])
@login_required
def merge_client_groups_api():
    data = request.json or {}
    name = data.get('name')
    domains = data.get('domains', [])
    if not name or not domains:
        return jsonify({'error': 'Missing name or domains to merge'}), 400
    try:
        success = sql_logger.merge_client_entities(name, domains)
        if success:
            return jsonify({'success': True})
        return jsonify({'error': 'Failed to merge client groups'}), 500
    except Exception as e:
        logger.error(f"Error merging client groups: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/client_groups/<int:group_id>', methods=['DELETE'])
@login_required
def delete_client_group_api(group_id):
    try:
        success = sql_logger.delete_client_group(group_id)
        if success:
            return jsonify({'success': True})
        return jsonify({'error': 'Failed to delete client group'}), 500
    except Exception as e:
        logger.error(f"Error deleting client group: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/holidays/import', methods=['POST'])
@login_required
def import_holidays_api():
    data = request.json or {}
    text = data.get('text', '')
    if not text:
        return jsonify({'error': 'No holiday list text provided'}), 400
    try:
        parsed_holidays = ai_agent.parse_pasted_holidays(text)
        added = 0
        updated = 0
        merged = 0
        skipped = 0
        
        for item in parsed_holidays:
            date_str = item['date']
            holiday_name = item['holiday']
            res = sql_logger.add_holiday(date_str, holiday_name)
            status = res.get('status')
            if status == 'added':
                added += 1
            elif status == 'merged':
                merged += 1
            elif status == 'skipped':
                skipped += 1
            elif status == 'updated':
                updated += 1
                
        return jsonify({
            'success': True,
            'summary': {
                'added': added,
                'updated': updated,
                'merged': merged,
                'skipped': skipped
            }
        })
    except Exception as e:
        logger.error(f"Error parsing/importing holidays: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/regenerate_ai', methods=['POST'])
@login_required
def regenerate_ai():
    """DEPRECATED: Use /api/regenerate_ai_sync instead for low-latency updates"""
    return jsonify({'success': False, 'error': 'This endpoint is deprecated. Please refresh your dashboard.'}), 410

def legacy_regenerate_ai_DEPRECATED():
    """Flag a ticket for AI regeneration"""
    data = request.json
    ticket_id = data.get('ticket_id')
    
    if not ticket_id:
        return jsonify({'success': False, 'error': 'No ticket ID provided'}), 400
        
    success = sql_logger.flag_for_ai_regeneration(ticket_id)
    if success:
        socketio.emit('ticket_updated')
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Database error flagging ticket'}), 500


@app.route('/api/regenerate_ai_sync', methods=['POST'])
@login_required
def regenerate_ai_sync():
    """Synchronously regenerate AI draft with optional user instructions"""
    try:
        data = request.json
        ticket_id = data.get('ticket_id')
        user_instructions = data.get('user_instructions')
        current_draft = data.get('current_draft')
        
        if not ticket_id:
            return jsonify({'success': False, 'error': 'No ticket ID provided'}), 400
            
        # 1. Fetch thread messages
        messages = sql_logger.get_thread_messages(ticket_id)
        if not messages:
            return jsonify({'success': False, 'error': 'No messages found for ticket'}), 404
            
        # 2. Get last customer message for query context
        last_customer_msg = next((m for m in reversed(messages) if not m.get('is_internal')), messages[-1])
        query = last_customer_msg.get('body_text', '')
        
        # 3. DUAL RAG SEARCH
        # 3a. Search documentation (authoritative)
        docs_raw = get_documentation_db().query_bookstack(query, n_results=Config.TOP_K_RESULTS)
        # Format doc results to match OpenAIAgent expectations
        docs = []
        if docs_raw and 'documents' in docs_raw and docs_raw['documents']:
            for i, doc_text in enumerate(docs_raw['documents'][0]):
                docs.append({
                    'content': doc_text,
                    'metadata': docs_raw['metadatas'][0][i] if 'metadatas' in docs_raw else {},
                    'distance': docs_raw['distances'][0][i] if 'distances' in docs_raw else 0.5
                })

        # 3b. Search experience (advisory)
        experiences = get_experience_db().search_similar(query, top_k=Config.TOP_K_RESULTS)
        
        # 4. Generate response
        draft, provenance = ai_agent.generate_response(
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
            
        # 5. Save draft to DB immediately
        update_payload = {
            'ai_draft': draft,
            'last_updated': datetime.now().isoformat()
        }
        
        # Get DB primary key internal ID for this ticket_id
        ticket = sql_logger.get_ticket_by_id(ticket_id)
        if ticket:
            actor = session.get('user', {}).get('username', 'system')
            sql_logger.update_ticket_fields(ticket['id'], update_payload, actor=actor)
            
        # 6. Broadcast update via socket
        socketio.emit('ticket_updated', {
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


# ============================================================================
# WebSocket Events - All Protected by Authentication
# ============================================================================

@socketio.on('connect')
@socketio_login_required
def handle_connect():
    """Handle new client connection"""
    user_id = session.get('user_id', 'anonymous')
    username = session.get('user', {}).get('username', 'unknown')
    
    connected_users[request.sid] = {
        'user_id': user_id,
        'username': username,
        'connected_at': datetime.now().isoformat()
    }
    
    emit('connection_established', {
        'user_id': user_id,
        'username': username,
        'message': 'Connected to dashboard'
    })
    
    # Broadcast user count update to ALL clients
    socketio.emit('user_count_update', {
        'count': len(connected_users)
    })
    
    logger.info(f"✅ User {username} ({user_id}) connected. Total users: {len(connected_users)}")


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    user_info = connected_users.get(request.sid, {})
    user_id = user_info.get('user_id', 'unknown')
    username = user_info.get('username', 'unknown')
    
    # Release any locks held by this user
    tickets_to_unlock = [
        ticket_id for ticket_id, lock_info in user_locks.items()
        if lock_info['user_id'] == user_id
    ]
    
    for ticket_id in tickets_to_unlock:
        del user_locks[ticket_id]
        socketio.emit('ticket_unlocked', {'ticket_id': ticket_id})
    
    # Remove user from connected users
    if request.sid in connected_users:
        del connected_users[request.sid]
    
    # Broadcast user count update
    socketio.emit('user_count_update', {
        'count': len(connected_users)
    })
    
    print(f"User {username} ({user_id}) disconnected. Total users: {len(connected_users)}")
    logger.info(f"❌ User {username} ({user_id}) disconnected. Total users: {len(connected_users)}")


@socketio.on('lock_ticket')
@socketio_login_required
def handle_lock_ticket(data):
    """Lock a ticket for editing"""
    ticket_id = data.get('ticket_id')
    user_id = session.get('user_id', 'anonymous')
    username = session.get('user', {}).get('username', 'unknown')
    
    logger.info(f"🔒 User {username} is attempting to lock ticket {ticket_id}")
    
    if not ticket_id:
        emit('lock_failed', {'error': 'No ticket ID provided'})
        return
    
    # Check if already locked by another user
    if ticket_id in user_locks and user_locks[ticket_id]['user_id'] != user_id:
        emit('lock_failed', {
            'error': 'Ticket is locked by another user',
            'locked_by': user_locks[ticket_id]['username']
        })
        return
    
    # Lock the ticket
    user_locks[ticket_id] = {
        'user_id': user_id,
        'username': username,
        'locked_at': datetime.now().isoformat()
    }
    
    emit('lock_acquired', {'ticket_id': ticket_id})
    
    # Broadcast to other users that ticket is locked (skip sender)
    emit('ticket_locked', {
        'ticket_id': ticket_id,
        'locked_by': user_id,
        'locked_by_username': username
    }, skip_sid=request.sid, broadcast=True)


@socketio.on('unlock_ticket')
@socketio_login_required
def handle_unlock_ticket(data):
    """Unlock a ticket"""
    ticket_id = data.get('ticket_id')
    user_id = session.get('user_id', 'anonymous')
    username = session.get('user', {}).get('username', 'unknown')
    
    logger.info(f"🔓 User {username} is unlocking ticket {ticket_id}")
    
    if not ticket_id:
        return
    
    # Only allow unlocking if user owns the lock
    if ticket_id in user_locks and user_locks[ticket_id]['user_id'] == user_id:
        del user_locks[ticket_id]
        
        emit('lock_released', {'ticket_id': ticket_id})
        
        # Broadcast to other users that ticket is unlocked (skip sender)
        emit('ticket_unlocked', {
            'ticket_id': ticket_id
        }, skip_sid=request.sid, broadcast=True)


@socketio.on('refresh_tickets')
@socketio_login_required
def handle_refresh_tickets():
    """Handle manual refresh request from client"""
    username = session.get('user', {}).get('username', 'unknown')
    logger.info(f"🔄 Ticket refresh requested by {username}")
    tickets = sql_logger.get_active_tickets()
    
    # Normalize statuses and add lock information
    for ticket in tickets:
        ticket['status'] = normalize_legacy_status(ticket.get('status'))
        
        ticket_id = ticket.get('ticket_id')
        if ticket_id in user_locks:
            ticket['locked_by'] = user_locks[ticket_id]['user_id']
            ticket['locked_by_username'] = user_locks[ticket_id]['username']
            ticket['locked_at'] = user_locks[ticket_id]['locked_at']
        else:
            ticket['locked_by'] = None
    
    stats = calculate_stats(tickets)
    
    emit('tickets_refreshed', {
        'tickets': tickets,
        'stats': stats
    })
@socketio.on('typing_update')
@socketio_login_required
def handle_typing_update(data):
    """Broadcast that a user is typing in a ticket"""
    ticket_id = data.get('ticket_id')
    user_id = session.get('user_id', 'anonymous')
    username = session.get('user', {}).get('username', 'unknown')
    is_typing = data.get('is_typing', False)
    
    if ticket_id:
        # Broadcast to other users (skip sender)
        emit('user_typing', {
            'ticket_id': ticket_id,
            'user_id': user_id,
            'username': username,
            'is_typing': is_typing
        }, skip_sid=request.sid, broadcast=True)


@socketio.on('get_staff_metrics')
@socketio_admin_required
def handle_get_staff_metrics(data):
    """Get metrics for all staff members with time range"""
    time_range = data.get('time_range', 'all')
    from_date = data.get('from_date')
    to_date = data.get('to_date')
    
    users = auth.user_manager.list_users()
    staff_usernames = [u['username'] for u in users if u['role'] in ['staff', 'admin']]
    
    metrics = sql_logger.get_all_staff_metrics(staff_usernames, time_range, from_date, to_date)
    emit('staff_metrics_update', {
        'metrics': metrics, 
        'time_range': time_range,
        'from_date': from_date,
        'to_date': to_date
    })


@socketio.on('get_client_stats')
@socketio_admin_required
def handle_get_client_stats(data):
    """Get metrics grouped by domain with time range"""
    time_range = data.get('time_range', 'all')
    from_date = data.get('from_date')
    to_date = data.get('to_date')
    stats = sql_logger.get_client_stats(time_range, from_date, to_date)
    emit('client_stats_update', {
        'stats': stats, 
        'time_range': time_range,
        'from_date': from_date,
        'to_date': to_date
    })


@socketio.on('get_drilldown_metrics')
@socketio_admin_required
def handle_get_drilldown_metrics(data):
    """Get metrics for drilldown modal, optionally filtered by status"""
    m_type = data.get('type')
    m_id = data.get('id')
    status = data.get('status')
    time_range = data.get('time_range', 'all')
    from_date = data.get('from_date')
    to_date = data.get('to_date')
    
    if m_type == 'staff':
        metrics = sql_logger.get_staff_metrics(m_id, time_range, from_date, to_date, status)
    else:
        client_stats = sql_logger.get_client_stats(time_range, from_date, to_date, status, domain_filter=m_id)
        metrics = client_stats[0] if client_stats else {}
        
    emit('drilldown_metrics_update', {
        'type': m_type,
        'id': m_id,
        'status': status,
        'metrics': metrics
    })


@socketio.on('get_ticket_timeline')
@socketio_login_required
def handle_get_ticket_timeline(data):
    """Get SLA tracking timeline for a ticket"""
    ticket_id = data.get('ticket_id')
    if not ticket_id: return
    timeline = sql_logger.get_ticket_timeline(ticket_id)
    emit('ticket_timeline_update', {'ticket_id': ticket_id, 'timeline': timeline})


@socketio.on('get_settings')
@socketio_admin_required
def handle_get_settings():
    """Get current .env settings for the management UI"""
    env_vars = env_manager.read_env()
    # Filter for relevant settings to display
    display_vars = {
        'ENABLE_TICKET_CLEANUP_DAEMON': env_vars.get('ENABLE_TICKET_CLEANUP_DAEMON', 'True'),
        'INTERNAL_NOTE_AS_RESPONSE': env_vars.get('INTERNAL_NOTE_AS_RESPONSE', 'False'),
        'SOFT_DELETE_CLOSED_AFTER_DAYS': env_vars.get('SOFT_DELETE_CLOSED_AFTER_DAYS', '14'),
        'HARD_DELETE_AFTER_DAYS': env_vars.get('HARD_DELETE_AFTER_DAYS', '16'),
        'POLLING_INTERVAL': env_vars.get('POLLING_INTERVAL', '300'),
        'TEST_MODE': env_vars.get('TEST_MODE', 'False'),
        'TEST_EMAIL': env_vars.get('TEST_EMAIL', 'bilal.khan@greenwaresolutions.com'),
        'TEST_CC': env_vars.get('TEST_CC', ''),
        'TEST_SUBJECT_TAG': env_vars.get('TEST_SUBJECT_TAG', '[TEST MODE] '),
        'USER_EMAIL': env_vars.get('USER_EMAIL', ''),
        'PROCESSING_DAYS_BACK': env_vars.get('PROCESSING_DAYS_BACK', '2'),
        'GENERATIONAL_MODEL': env_vars.get('GENERATIONAL_MODEL', 'gpt-5-mini'),
        'INTERPRETATION_MODEL': env_vars.get('INTERPRETATION_MODEL', 'gpt-4.1-nano-2025-04-14'),
        'ANALYSIS_MODEL': env_vars.get('ANALYSIS_MODEL', 'gpt-4.1-mini-2025-04-14'),
        'WEB_SEARCH_MODEL': env_vars.get('WEB_SEARCH_MODEL', 'gpt-4o-mini-2024-07-18')
    }
    emit('settings_update', {'settings': display_vars})


@socketio.on('update_settings')
@socketio_admin_required
def handle_update_settings(data):
    """Update .env settings"""
    if not data:
        return
    
    logger.info(f"⚙️ Received settings update request: {list(data.keys())}")
    
    try:
        # Filter data to only include valid settings keys to avoid polluting .env
        valid_keys = [
            'ENABLE_TICKET_CLEANUP_DAEMON', 'INTERNAL_NOTE_AS_RESPONSE', 'SOFT_DELETE_CLOSED_AFTER_DAYS',
            'HARD_DELETE_AFTER_DAYS', 'POLLING_INTERVAL', 'TEST_MODE',
            'TEST_EMAIL', 'TEST_CC', 'TEST_SUBJECT_TAG',
            'USER_EMAIL', 'PROCESSING_DAYS_BACK',
            'GENERATIONAL_MODEL', 'INTERPRETATION_MODEL', 'ANALYSIS_MODEL',
            'WEB_SEARCH_MODEL'
        ]
        filtered_data = {k: str(v) for k, v in data.items() if k in valid_keys}
        
        if not filtered_data:
            logger.warning("⚠️ No valid settings found in update request")
            emit('settings_saved', {'success': False, 'message': 'No valid settings provided'})
            return

        success = env_manager.update_vars(filtered_data)
        if success:
            Config.reload()
            logger.info("♻️ Config class reloaded with new settings")
        
        logger.info(f"✅ Settings update outcome: {'Success' if success else 'Failure'}")
        emit('settings_saved', {'success': success})
    except Exception as e:
        logger.error(f"❌ Error handling settings update: {e}")
        emit('settings_saved', {'success': False, 'message': str(e)})


@socketio.on('reassign_ticket')
@socketio_admin_required
def handle_reassign_ticket(data):
    """Change assignment of a ticket (admin only)"""
    ticket_id = data.get('ticket_id')
    new_assignee = data.get('assigned_to')
    
    if not ticket_id or not new_assignee:
        return
    actor_username = session.get('user', {}).get('username', 'system')
    success = sql_logger.update_ticket_fields(ticket_id, {'assigned_to': new_assignee}, actor=actor_username)
    
    if success:
        # Broadcast to everyone
        socketio.emit('ticket_reassigned', {
            'ticket_id': ticket_id,
            'assigned_to': new_assignee,
            'updated_by': session.get('user', {}).get('username')
        })




if __name__ == '__main__':
    try:
        print("=" * 80)
        print("Starting Multi-User Dashboard with Custom Authentication")
        print("=" * 80)
        print(f"Dashboard URL: http://0.0.0.0:5000")
        print(f"Database: PostgreSQL ({Config.POSTGRES_HOST}:{Config.POSTGRES_PORT}/{Config.POSTGRES_DB})")
        print("=" * 80)
        
        # Run with SocketIO
        socketio.run(
            app,
            host='0.0.0.0',
            port=5000,
            debug=True,
            allow_unsafe_werkzeug=True
        )
    except KeyboardInterrupt:
        print("=" * 80)
        print('Dashboard stopped by user')
        print("=" * 80)