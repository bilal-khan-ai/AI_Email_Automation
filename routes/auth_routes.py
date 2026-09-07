# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - Auth & User Management Blueprint - start
"""
routes/auth_routes.py

Authentication and user management HTTP routes:
- Login / Logout
- Admin user CRUD, status/assignment toggles, password updates
- User self password change
"""

import re
import secrets
from flask import Blueprint, render_template, jsonify, request, session, redirect, url_for, flash
import auth
from auth import admin_required, login_required, get_current_user
from dashboard_context import sql_logger, calculate_stats

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login page with username/password authentication."""
    # If already logged in, redirect to dashboard
    if 'user' in session:
        return redirect(url_for('core.index'))
    
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
            
            return redirect(url_for('core.index'))
        else:
            flash('Invalid username or password', 'danger')
            return render_template('login.html')
    
    return render_template('login.html')


@auth_bp.route('/logout')
def logout():
    """Logout user and clear session state."""
    username = session.get('user', {}).get('username', 'User')
    session.clear()
    flash(f'Goodbye, {username}!', 'success')
    return redirect(url_for('auth.login'))


@auth_bp.route('/management')
@admin_required
def management():
    """Admin management page for user management and global stats."""
    users = auth.user_manager.list_users()
    # Filter only users who are marked as assignable (Staff or Admin)
    staff_members = [u for u in users if u['role'] in ['staff', 'admin']]
    
    # Get stats for all tickets for admin overview
    all_tickets = sql_logger.get_active_tickets()
    global_stats = calculate_stats(all_tickets)
    
    return render_template('management.html', 
                          user=get_current_user(), 
                          users=users, 
                          staff_members=staff_members,
                          global_stats=global_stats)


@auth_bp.route('/admin/users/toggle-status/<username>', methods=['POST'])
@admin_required
def admin_toggle_user_status(username):
    """Enable/Disable a user login access (admin only)."""
    if username == session.get('user', {}).get('username'):
        flash('❌ You cannot disable your own account', 'danger')
        return redirect(url_for('auth.management'))
    
    if auth.user_manager.toggle_user_status(username):
        user = auth.user_manager.get_user(username)
        status = "enabled" if user['is_active'] else "disabled"
        flash(f'✅ Login access for "{username}" has been {status}', 'success')
    else:
        flash(f'❌ Failed to toggle account status for user "{username}"', 'danger')
    
    return redirect(url_for('auth.management'))


@auth_bp.route('/admin/users/toggle-assignable/<username>', methods=['POST'])
@admin_required
def admin_toggle_user_assignment(username):
    """Enable/Disable ticket assignment for a user (admin only)."""
    if auth.user_manager.toggle_assignable_status(username):
        user = auth.user_manager.get_user(username)
        status = "enabled" if user['is_assignable'] else "disabled"
        flash(f'✅ Ticket assignment for "{username}" has been {status}', 'success')
    else:
        flash(f'❌ Failed to toggle assignment status for user "{username}"', 'danger')
    
    return redirect(url_for('auth.management'))


@auth_bp.route('/admin/users/create', methods=['POST'])
@admin_required
def admin_create_user():
    """Create a new user (admin only)."""
    username = request.form.get('username')
    password = request.form.get('password')
    role = request.form.get('role', 'staff')
    
    if not username or not password:
        flash('Username and password are required', 'danger')
        return redirect(url_for('auth.management'))
    
    if auth.user_manager.create_user(username, password, role):
        flash(f'✅ User "{username}" created successfully (Role: {role})', 'success')
    else:
        flash(f'❌ Failed to create user "{username}" (username may already exist)', 'danger')
    
    return redirect(url_for('auth.management'))


@auth_bp.route('/admin/users/<username>/delete', methods=['POST'])
@admin_required
def admin_delete_user(username):
    """Delete a user (admin only)."""
    if username == session.get('user', {}).get('username'):
        flash('❌ You cannot delete your own account', 'danger')
        return redirect(url_for('auth.management'))
    
    if auth.user_manager.delete_user(username):
        flash(f'✅ User "{username}" has been deleted', 'success')
    else:
        flash(f'❌ Failed to delete user "{username}"', 'danger')
    
    return redirect(url_for('auth.management'))


@auth_bp.route('/admin/users/<username>/change-password', methods=['POST'])
@admin_required
def admin_change_password(username):
    """Change user password (admin only, restrictions apply)."""
    # Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - Require old password verification and confirmation - start
    old_password = request.form.get('old_password')
    new_password = request.form.get('new_password')
    confirm_password = request.form.get('confirm_password')
    current_user = get_current_user()
    target_user = auth.user_manager.get_user(username)
    
    if not new_password:
        flash('New password is required', 'danger')
        return redirect(url_for('auth.management'))

    if confirm_password and new_password != confirm_password:
        flash('❌ New password and confirmation do not match', 'danger')
        return redirect(url_for('auth.management'))

    if len(new_password) < 6:
        flash('❌ Password must be at least 6 characters long', 'danger')
        return redirect(url_for('auth.management'))

    # If admin is changing their own password, verify old password
    if username == current_user['username']:
        if not old_password or not auth.user_manager.authenticate_user(username, old_password):
            flash('❌ Current (old) password is incorrect', 'danger')
            return redirect(url_for('auth.management'))

    # BLOCK: Admin to Admin password changing (unless it's yourself)
    if target_user and target_user['role'] == 'admin' and username != current_user['username']:
        flash('❌ You do not have permission to change another administrator\'s password', 'danger')
        return redirect(url_for('auth.management'))
    
    if auth.user_manager.update_password(username, new_password):
        flash(f'✅ Password changed for user "{username}"', 'success')
    else:
        flash(f'❌ Failed to change password for user "{username}"', 'danger')
    
    return redirect(url_for('auth.management'))


@auth_bp.route('/api/user/change-password', methods=['POST'])
@login_required
def user_change_password():
    """Route for any logged-in user to change their own password with old password verification."""
    old_password = request.form.get('old_password')
    new_password = request.form.get('new_password')
    confirm_password = request.form.get('confirm_password')
    current_user = get_current_user()
    
    if not old_password:
        return jsonify({'error': 'Current (old) password is required'}), 400

    if not new_password:
        return jsonify({'error': 'New password is required'}), 400

    if confirm_password and new_password != confirm_password:
        return jsonify({'error': 'New password and confirmation password do not match'}), 400
    
    if len(new_password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters long'}), 400

    # Verify old password
    user_auth = auth.user_manager.authenticate_user(current_user['username'], old_password)
    if not user_auth:
        return jsonify({'error': 'Current (old) password is incorrect'}), 400

    if auth.user_manager.update_password(current_user['username'], new_password):
        return jsonify({'success': True, 'message': 'Password updated successfully'})
    else:
        return jsonify({'error': 'Failed to update password'}), 500


@auth_bp.route('/api/users/quick-add', methods=['POST'])
@login_required
def quick_add_user():
    """Quickly register an organization employee and make them assignable. Admin only."""
    import os
    current_user_role = session.get('user', {}).get('role', 'staff')
    if current_user_role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    data = request.get_json(silent=True) or request.form
    first_name = (data.get('first_name') or '').strip()
    last_name = (data.get('last_name') or '').strip()
    role = (data.get('role') or 'staff').strip().lower()
    if role not in ['staff', 'admin']:
        role = 'staff'

    clean_first = re.sub(r'[^a-zA-Z0-9]', '', first_name).lower()
    clean_last = re.sub(r'[^a-zA-Z0-9]', '', last_name).lower()

    if not clean_first or not clean_last:
        return jsonify({'error': 'Both first name and last name are required to generate the employee ID.'}), 400

    username = f"{clean_first}_{clean_last}"

    existing = auth.user_manager.get_user(username)
    if existing:
        return jsonify({
            'success': True,
            'already_existed': True,
            'user': {
                'id': existing.get('id'),
                'username': existing.get('username'),
                'role': existing.get('role'),
                'is_assignable': existing.get('is_assignable', True)
            },
            'message': f"Employee '{username}' is already registered."
        })

    default_password = os.environ.get('DEFAULT_USER_PASSWORD') or os.environ.get('QUICK_ADD_PASSWORD')
    if not default_password:
        return jsonify({'error': 'DEFAULT_USER_PASSWORD is not configured in environment.'}), 500

    created = auth.user_manager.create_user(username=username, password=default_password, role=role)
    if created:
        new_user = auth.user_manager.get_user(username)
        return jsonify({
            'success': True,
            'already_existed': False,
            'user': {
                'id': new_user.get('id') if new_user else None,
                'username': username,
                'role': role,
                'is_assignable': True
            },
            'message': f"Employee '{username}' registered successfully."
        })
    else:
        return jsonify({'error': f"Failed to register employee '{username}'"}), 500


__all__ = ['auth_bp']
