# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - Admin Config & Holidays Blueprint - start
"""
routes/admin_config_routes.py

Admin configuration & reference data HTTP routes:
- Business holidays CRUD & NLP bulk import (/api/holidays, /api/holidays/import)
- Client entity groups CRUD & domain merging (/api/client_groups, /api/client_groups/merge, /api/client_groups/<id>)
"""

import logging
from flask import Blueprint, jsonify, request

from auth import login_required
from dashboard_context import sql_logger, get_ai_agent
from services.env_manager import EnvManager

logger = logging.getLogger(__name__)

admin_config_bp = Blueprint('admin_config', __name__)
env_manager = EnvManager()


# Bilal Khan (01/09/2026) Issue No  Sheet_Name  - System settings REST endpoint for management console - start
@admin_config_bp.route('/api/settings', methods=['GET'])
@login_required
def get_settings_api():
    """Get system .env settings for management console."""
    try:
        env_vars = env_manager.read_env()
        display_vars = {
            # Email & Polling
            'USER_EMAIL': env_vars.get('USER_EMAIL', ''),
            'POLLING_INTERVAL': env_vars.get('POLLING_INTERVAL', '600'),
            'PROCESSING_DAYS_BACK': env_vars.get('PROCESSING_DAYS_BACK', '2'),
            # Test Mode & Safe Sandbox
            'TEST_MODE': env_vars.get('TEST_MODE', 'False'),
            'TEST_EMAIL': env_vars.get('TEST_EMAIL', 'bilal.khan@greenwaresolutions.com'),
            'TEST_CC': env_vars.get('TEST_CC', ''),
            'TEST_SUBJECT_TAG': env_vars.get('TEST_SUBJECT_TAG', '[TEST MODE] '),
            # AI & Automation Controls
            'ENABLE_AI': env_vars.get('ENABLE_AI', 'False'),
            'ENABLE_RAG': env_vars.get('ENABLE_RAG', 'False'),
            'AUTO_GENERATE_RESPONSES': env_vars.get('AUTO_GENERATE_RESPONSES', 'True'),
            'TOP_K_RESULTS': env_vars.get('TOP_K_RESULTS', '5'),
            # Retention & Cleanup Daemon
            'ENABLE_TICKET_CLEANUP_DAEMON': env_vars.get('ENABLE_TICKET_CLEANUP_DAEMON', 'True'),
            'SOFT_DELETE_CLOSED_AFTER_DAYS': env_vars.get('SOFT_DELETE_CLOSED_AFTER_DAYS', '1'),
            'HARD_DELETE_AFTER_DAYS': env_vars.get('HARD_DELETE_AFTER_DAYS', '6'),
            'CLEANUP_DAEMON_INTERVAL_SECONDS': env_vars.get('CLEANUP_DAEMON_INTERVAL_SECONDS', '3600'),
            'DAYS_TO_KEEP_TICKET': env_vars.get('DAYS_TO_KEEP_TICKET', '30'),
            # SLA & Data Formatting
            'INTERNAL_NOTE_AS_RESPONSE': env_vars.get('INTERNAL_NOTE_AS_RESPONSE', 'False'),
            'TABLES_INCLUDE_PREVIEW': env_vars.get('TABLES_INCLUDE_PREVIEW', 'False'),
            'TABLES_PREVIEW_ROWS': env_vars.get('TABLES_PREVIEW_ROWS', '8'),
            # Azure DevOps Integration
            'AZURE_DEVOPS_ORG': env_vars.get('AZURE_DEVOPS_ORG', ''),
            'AZURE_DEVOPS_PROJECT': env_vars.get('AZURE_DEVOPS_PROJECT', ''),
            'AZURE_DEVOPS_POLLING_INTERVAL': env_vars.get('AZURE_DEVOPS_POLLING_INTERVAL', '600')
        }
        return jsonify({'settings': display_vars})
    except Exception as e:
        logger.error(f"Error reading settings: {e}")
        return jsonify({'error': str(e)}), 500
# Bilal Khan (01/09/2026) Issue No  Sheet_Name  - System settings REST endpoint for management console - end


@admin_config_bp.route('/api/holidays', methods=['GET'])
@login_required
def get_holidays_api():
    """List all registered business holidays."""
    try:
        holidays = sql_logger.get_holidays()
        return jsonify({'holidays': holidays})
    except Exception as e:
        logger.error(f"Error fetching holidays: {e}")
        return jsonify({'error': str(e)}), 500


@admin_config_bp.route('/api/holidays', methods=['POST'])
@login_required
def add_holiday_api():
    """Register a new business holiday."""
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


@admin_config_bp.route('/api/holidays', methods=['PUT'])
@login_required
def update_holiday_api():
    """Update date or label for an existing holiday."""
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


@admin_config_bp.route('/api/holidays', methods=['DELETE'])
@login_required
def delete_holidays_api():
    """Batch delete registered holidays."""
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


# Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Format unified client groups and unmerged domain entities - start
@admin_config_bp.route('/api/client_groups', methods=['GET'])
@login_required
def get_client_groups_api():
    """List all registered client business entities and domain mappings."""
    try:
        raw_data = sql_logger.get_all_client_entities()
        formatted_groups = []
        if isinstance(raw_data, dict):
            for g in raw_data.get('groups', []):
                formatted_groups.append({
                    'id': g.get('id'),
                    'name': g.get('name'),
                    'domains': g.get('domains', ''),
                    'type': 'group'
                })
            for d in raw_data.get('unmerged_domains', []):
                formatted_groups.append({
                    'id': None,
                    'name': d,
                    'domains': d,
                    'type': 'domain'
                })
        elif isinstance(raw_data, list):
            formatted_groups = raw_data
        return jsonify({'groups': formatted_groups})
    except Exception as e:
        logger.error(f"Error fetching client groups: {e}")
        return jsonify({'error': str(e)}), 500
# Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Format unified client groups and unmerged domain entities - end


@admin_config_bp.route('/api/client_groups', methods=['POST'])
@login_required
def create_client_group_api():
    """Create a new client entity group with associated email domains."""
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


@admin_config_bp.route('/api/client_groups/merge', methods=['POST'])
@login_required
def merge_client_groups_api():
    """Merge client domains into an existing or new client group."""
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


@admin_config_bp.route('/api/client_groups/<int:group_id>', methods=['DELETE'])
@login_required
def delete_client_group_api(group_id):
    """Delete a client entity group."""
    try:
        success = sql_logger.delete_client_group(group_id)
        if success:
            return jsonify({'success': True})
        return jsonify({'error': 'Failed to delete client group'}), 500
    except Exception as e:
        logger.error(f"Error deleting client group: {e}")
        return jsonify({'error': str(e)}), 500


@admin_config_bp.route('/api/holidays/import', methods=['POST'])
@login_required
def import_holidays_api():
    """Parse unstructured text with AI and import holidays into database."""
    data = request.json or {}
    text = data.get('text', '')
    if not text:
        return jsonify({'error': 'No holiday list text provided'}), 400
    try:
        agent = get_ai_agent()
        if not agent:
            return jsonify({'error': 'AI agent is not available for parsing'}), 503

        parsed_holidays = agent.parse_pasted_holidays(text)
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

__all__ = ['admin_config_bp']
# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - Admin Config & Holidays Blueprint - end
