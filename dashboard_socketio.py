import os
import io
import time
import secrets
import threading
import base64
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import Flask, render_template, jsonify, request, send_file, session, redirect, url_for, flash
from flask_socketio import SocketIO, emit, join_room, leave_room
from modules.sql_logger import SQLLogger
from modules.graph_connector import GraphConnector
import auth
from auth import init_auth, login_required, socketio_login_required, socketio_admin_required, admin_required, get_current_user, is_admin
from config import Config

app = Flask(__name__)

# UPDATED: Initialize custom authentication
app.config['SECRET_KEY'] = Config.SECRET_KEY
init_auth(app)  # Initialize custom authentication system

# Initialize SocketIO with async mode
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='threading',
    logger=False,
    engineio_logger=False
)

# Ensure data directory exists
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

# Initialize SQL logger (db_file argument is ignored by PostgreSQL backend)
sql_logger = SQLLogger("postgres")
sql_logger.authenticate()

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
    print("⚠️  WARNING: Failed to authenticate with Microsoft Graph at startup")
    print("    Attachment downloads and email sending may fail")
else:
    print("✅ Global Graph connector authenticated successfully")

# Create uploads directory for temporary file storage
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Track connected users and their rooms
connected_users = {}
user_locks = {}  # Track which tickets are being edited by which users


def normalize_legacy_status(status: str) -> str:
    """
    Map legacy status values to Open/Closed semantics.
    
    Args:
        status: Legacy status value
        
    Returns:
        Normalized status ('Open' or 'Closed')
    """
    if not status:
        return 'Open'
    
    status_lower = status.lower()
    
    # Map legacy statuses
    if status_lower in ['resolved', 'completed', 'closed']:
        return 'Closed'
    elif status_lower in ['pending review', 'pending', 'open', 'in progress']:
        return 'Open'
    else:
        # Default unknown statuses to Open
        return 'Open'


def calculate_stats(tickets, user=None):
    """
    Calculate ticket statistics with Open/Closed semantics.
    If user is staff, stats will naturally be filtered by the tickets passed in.
    """
    total = len(tickets)
    open_count = sum(1 for t in tickets if normalize_legacy_status(t.get('status')) == 'Open')
    closed_count = sum(1 for t in tickets if normalize_legacy_status(t.get('status')) == 'Closed')
    
    return {
        'total': total,
        'open': open_count,
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
            
            if user['role'] == 'admin':
                return redirect(url_for('management'))
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
    # Filter staff members (and assignable admins) for metrics
    staff_members = [u for u in users if u['role'] == 'staff' or (u['role'] == 'admin' and u.get('is_assignable'))]
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
    if user['role'] == 'admin' and request.args.get('view') != 'staff':
        return redirect(url_for('management'))
    
    # Get staff list for assignment dropdown
    users = auth.user_manager.list_users()
    staff_members = [u for u in users if u['role'] == 'staff']
    
    # Pass whether the user is an admin to the template
    return render_template('dashboard.html', user=user, staff_members=staff_members, is_admin=(user['role'] == 'admin'))


@app.route('/api/tickets')
@login_required
def get_tickets():
    """Get tickets with normalized statuses, filtered for staff if applicable"""
    user = get_current_user()
    
    # If staff, only show their assigned tickets
    if user.get('role') == 'staff':
        tickets = sql_logger.get_active_tickets(assigned_to=user['username'])
    else:
        # Admins see everything (though they usually use management page, 
        # but if they visit dashboard they see all)
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
    
    # Add assignment if provided
    if 'assigned_to' in data:
        update_payload['assigned_to'] = data.get('assigned_to')

    # Perform SQL Update using the database primary key
    success = sql_logger.update_ticket_fields(ticket_db_id, update_payload)

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


@app.route('/api/toggle_ticket_status', methods=['POST'])
@login_required
def toggle_ticket_status():
    """
    Toggle ticket status between Open and Closed.
    
    This is the ONLY endpoint that changes ticket status.
    """
    data = request.json
    ticket_db_id = data.get('row_number')  # This is the database primary key (id)
    ticket_id = data.get('ticket_id')
    user_id = session.get('user_id')
    
    if not ticket_db_id:
        return jsonify({'error': 'Row number required'}), 400

    # Get current ticket to determine current status
    ticket = sql_logger.get_ticket_by_id(ticket_id)
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404

    # Normalize and toggle status
    current_status = normalize_legacy_status(ticket.get('status'))
    new_status = 'Closed' if current_status == 'Open' else 'Open'

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

        # Send email
        success = graph_connector.send_email(
            user_email=Config.USER_EMAIL,
            recipient=Config.TEST_EMAIL if Config.TEST_MODE else send_to,
            subject=f"Re: {ticket['subject']}",
            body=ai_response,
            conversation_id=ticket.get('conversation_id'),
            cc=cc,
            bcc=bcc,
            attachments=attachments
        )

        if not success:
            return jsonify({'error': 'Failed to send email via Graph API'}), 500

        # Update ticket (NO row_number garbage)
        update_payload = {
            'ai_draft': ai_response,
            'last_updated': datetime.now().isoformat()
        }

        if assigned_to:
            update_payload['assigned_to'] = assigned_to

        sql_logger.update_ticket_fields(ticket['id'], update_payload)

        socketio.emit('email_sent', {
            'ticket_id': ticket_id
        })

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error sending email: {e}")
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


@app.route('/api/download_attachment', methods=['GET'])
@login_required
def download_attachment():
    """
    Download attachment from Microsoft Graph.
    Uses global graph_connector instance (no re-authentication per request).
    """
    try:
        user_email = request.args.get('user_email', Config.USER_EMAIL)
        message_id = request.args.get('message_id')
        attachment_id = request.args.get('attachment_id')
        attachment_name = request.args.get('attachment_name', 'attachment')

        if not message_id or not attachment_id:
            return jsonify({'error': 'Missing message_id or attachment_id'}), 400

        # Fetch attachment binary data from Graph API using pre-authenticated instance
        attachment_data = graph_connector.get_attachment_sync(
            user_email=user_email,
            message_id=message_id,
            attachment_id=attachment_id
        )

        if not attachment_data:
            return jsonify({'error': 'Attachment not found or failed to download'}), 404

        # Return raw binary data with proper headers
        return send_file(
            io.BytesIO(attachment_data),
            as_attachment=True,
            download_name=secure_filename(attachment_name),
            mimetype="application/octet-stream"
        )

    except Exception as e:
        print(f"Error downloading attachment: {e}")
        return jsonify({'error': f'Failed to download attachment: {str(e)}'}), 500


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
    
    print(f"User {username} ({user_id}) connected. Total users: {len(connected_users)}")


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


@socketio.on('lock_ticket')
@socketio_login_required
def handle_lock_ticket(data):
    """Lock a ticket for editing"""
    ticket_id = data.get('ticket_id')
    user_id = session.get('user_id', 'anonymous')
    username = session.get('user', {}).get('username', 'unknown')
    
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
    
    users = auth.user_manager.list_users()
    staff_usernames = [u['username'] for u in users if u['role'] == 'staff' or (u['role'] == 'admin' and u.get('is_assignable'))]
    
    metrics = sql_logger.get_all_staff_metrics(staff_usernames, time_range)
    emit('staff_metrics_update', {'metrics': metrics, 'time_range': time_range})


@socketio.on('reassign_ticket')
@socketio_admin_required
def handle_reassign_ticket(data):
    """Change assignment of a ticket (admin only)"""
    ticket_id = data.get('ticket_id')
    new_assignee = data.get('assigned_to')
    
    if not ticket_id or not new_assignee:
        return
    
    success = sql_logger.update_ticket_fields(ticket_id, {'assigned_to': new_assignee})
    
    if success:
        # Broadcast to everyone
        socketio.emit('ticket_reassigned', {
            'ticket_id': ticket_id,
            'assigned_to': new_assignee,
            'updated_by': session.get('user', {}).get('username')
        })


@socketio.on('trigger_auto_assign')
@socketio_admin_required
def handle_trigger_auto_assign():
    """Auto-assign all open, unassigned tickets to the least busy staff"""
    # Fetch all tickets assigned to "Unassigned"
    tickets = sql_logger.get_active_tickets(assigned_to="Unassigned")
    # Filter only Open tickets
    open_unassigned = [t for t in tickets if normalize_legacy_status(t.get('status')) == 'Open']
    
    assigned_count = 0
    for ticket in open_unassigned:
        least_busy_staff = sql_logger.get_least_busy_staff()
        if least_busy_staff and least_busy_staff != 'Unassigned':
            success = sql_logger.update_ticket_fields(ticket['ticket_id'], {'assigned_to': least_busy_staff})
            if success:
                assigned_count += 1
                # Broadcast the assignment
                socketio.emit('ticket_reassigned', {
                    'ticket_id': ticket['ticket_id'],
                    'assigned_to': least_busy_staff,
                    'updated_by': session.get('user', {}).get('username')
                })
    
    if assigned_count > 0:
        emit('auto_assign_complete', {'success': True, 'count': assigned_count})
    else:
        emit('auto_assign_complete', {'success': False, 'message': 'No open tickets found or no active staff available.'})



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