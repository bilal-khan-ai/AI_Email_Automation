# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - Core Dashboard Views Blueprint - start
"""
routes/core_routes.py

Core dashboard HTTP routes:
- Index (/) main dashboard page
- Contacts API (/api/contacts)
- Config API (/api/config)
"""

import logging
from flask import Blueprint, render_template, jsonify
import auth
from auth import login_required, get_current_user
from config import Config
from dashboard_context import sql_logger

logger = logging.getLogger(__name__)

core_bp = Blueprint('core', __name__)


@core_bp.route('/')
@login_required
def index():
    """Main dashboard page - requires authentication."""
    user = get_current_user()
    
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


@core_bp.route('/api/contacts')
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
        all_contacts.sort()
        
        return jsonify({'contacts': all_contacts})
    except Exception as e:
        logger.error(f"Error fetching contacts: {e}")
        return jsonify({'error': str(e)}), 500


@core_bp.route('/api/config', methods=['GET'])
@login_required
def get_config():
    """Expose dynamic client-side configuration parameters to the frontend dashboard."""
    try:
        return jsonify({
            'INTERNAL_NOTE_AS_RESPONSE': Config.INTERNAL_NOTE_AS_RESPONSE,
            'USER_EMAIL': Config.USER_EMAIL,
            'TEST_MODE': Config.TEST_MODE
        })
    except Exception as e:
        logger.error(f"Error serving config: {e}")
        return jsonify({'error': str(e)}), 500

__all__ = ['core_bp']
# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - Core Dashboard Views Blueprint - end
