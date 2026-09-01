# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - SocketIO Event Handlers Module - start
"""
socketio_handlers.py

SocketIO real-time event handlers for the collaborative dashboard:
- User connection & disconnection lifecycle
- Real-time ticket locking / unlocking & typing indicator updates
- Ticket metrics & timeline retrieval
- Dynamic settings updates
- Real-time ticket reassignment
"""

import logging
from datetime import datetime
from flask import session, request
from flask_socketio import emit

import auth
from auth import socketio_login_required, socketio_admin_required
from config import Config
from dashboard_context import (
    sql_logger, env_manager, connected_users, user_locks,
    normalize_legacy_status, calculate_stats
)

logger = logging.getLogger(__name__)


def register_handlers(socketio) -> None:
    """
    Register all SocketIO event listeners onto the provided SocketIO application instance.
    
    Args:
        socketio: Initialized Flask-SocketIO server instance
    """

    @socketio.on('connect')
    @socketio_login_required
    def handle_connect():
        """Handle new client connection."""
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
        """Handle client disconnection and clean up user ticket locks."""
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
        
        logger.info(f"❌ User {username} ({user_id}) disconnected. Total users: {len(connected_users)}")


    @socketio.on('lock_ticket')
    @socketio_login_required
    def handle_lock_ticket(data):
        """Lock a ticket for concurrent editing protection."""
        ticket_id = (data or {}).get('ticket_id')
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
        """Unlock a ticket lock held by the current user."""
        ticket_id = (data or {}).get('ticket_id')
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
        """Handle manual refresh request from client."""
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
        """Broadcast real-time typing status in a ticket draft editor."""
        ticket_id = (data or {}).get('ticket_id')
        user_id = session.get('user_id', 'anonymous')
        username = session.get('user', {}).get('username', 'unknown')
        is_typing = (data or {}).get('is_typing', False)
        
        if ticket_id:
            emit('user_typing', {
                'ticket_id': ticket_id,
                'user_id': user_id,
                'username': username,
                'is_typing': is_typing
            }, skip_sid=request.sid, broadcast=True)


    @socketio.on('get_staff_metrics')
    @socketio_admin_required
    def handle_get_staff_metrics(data):
        """Get performance metrics for all staff members with time range."""
        data = data or {}
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
        """Get ticket volume and resolution metrics grouped by domain with time range."""
        data = data or {}
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
        """Get metrics for drilldown modal, optionally filtered by status."""
        data = data or {}
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
        """Get SLA tracking timeline for a ticket."""
        ticket_id = (data or {}).get('ticket_id')
        if not ticket_id:
            return
        timeline = sql_logger.get_ticket_timeline(ticket_id)
        emit('ticket_timeline_update', {'ticket_id': ticket_id, 'timeline': timeline})


    @socketio.on('get_settings')
    @socketio_admin_required
    def handle_get_settings():
        """Get current .env settings for the management UI."""
        env_vars = env_manager.read_env()
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
        """Update .env settings safely with whitelist filtering."""
        if not data:
            return
        
        logger.info(f"⚙️ Received settings update request: {list(data.keys())}")
        
        # Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Comprehensive settings whitelist for live .env updates - start
        try:
            valid_keys = [
                # Email & Polling
                'USER_EMAIL', 'POLLING_INTERVAL', 'PROCESSING_DAYS_BACK',
                # Test Mode & Safe Dispatch
                'TEST_MODE', 'TEST_EMAIL', 'TEST_CC', 'TEST_SUBJECT_TAG',
                # AI & Automation Engine
                'ENABLE_AI', 'ENABLE_RAG', 'AUTO_GENERATE_RESPONSES', 'TOP_K_RESULTS',
                # Ticket Retention & Cleanup
                'ENABLE_TICKET_CLEANUP_DAEMON', 'SOFT_DELETE_CLOSED_AFTER_DAYS',
                'HARD_DELETE_AFTER_DAYS', 'DAYS_TO_KEEP_TICKET',
                'CLEANUP_DAEMON_INTERVAL_SECONDS', 'CLEANUP_BATCH_SIZE',
                # SLA & Data Analytics
                'INTERNAL_NOTE_AS_RESPONSE', 'TABLES_INCLUDE_PREVIEW', 'TABLES_PREVIEW_ROWS',
                # Azure DevOps
                'AZURE_DEVOPS_ORG', 'AZURE_DEVOPS_PROJECT', 'AZURE_DEVOPS_POLLING_INTERVAL'
            ]
            filtered_data = {k: str(v) for k, v in data.items() if k in valid_keys}
            
            if not filtered_data:
                logger.warning("⚠️ No valid settings found in update request")
                emit('settings_saved', {'success': False, 'message': 'No valid settings provided'})
                return
        # Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Comprehensive settings whitelist for live .env updates - end

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
        """Change assignment of a ticket (admin only)."""
        data = data or {}
        ticket_id = data.get('ticket_id')
        new_assignee = data.get('assigned_to')
        
        if not ticket_id or not new_assignee:
            return
        actor_username = session.get('user', {}).get('username', 'system')
        success = sql_logger.update_ticket_fields(ticket_id, {'assigned_to': new_assignee}, actor=actor_username)
        
        if success:
            socketio.emit('ticket_reassigned', {
                'ticket_id': ticket_id,
                'assigned_to': new_assignee,
                'updated_by': session.get('user', {}).get('username')
            })

__all__ = ['register_handlers']
# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - SocketIO Event Handlers Module - end
