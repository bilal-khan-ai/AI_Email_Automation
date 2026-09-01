# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - Azure DevOps Blueprint - start
"""
routes/devops_routes.py

Azure DevOps integration HTTP routes:
- Linked work items listing (/api/devops/linked/<ticket_id>)
- Multi-project discovery (/api/devops/projects)
- Project metadata inspection (/api/devops/meta, /api/devops/meta/<path:project_name>)
- Direct attachment streaming upload (/api/devops/upload_attachment)
- Work item creation & ticket linking (/api/devops/create)
- Dynamic organization user search (/api/devops/users)
- Work item linking & unlinking (/api/devops/link, /api/devops/unlink)
"""

import re
import base64
import logging
from werkzeug.utils import secure_filename
from flask import Blueprint, jsonify, request, session

from auth import login_required
from config import Config
from dashboard_context import (
    sql_logger, devops_connector, get_socketio
)

logger = logging.getLogger(__name__)

devops_bp = Blueprint('devops', __name__)


@devops_bp.route('/api/devops/linked/<ticket_id>')
@login_required
def get_linked_devops_items(ticket_id):
    """Fetch details of all Azure DevOps work items linked to a ticket."""
    try:
        item_ids = sql_logger.get_ticket_devops_work_items(ticket_id)
        if not item_ids:
            return jsonify({'work_items': [], 'configured': devops_connector.is_configured})

        items = devops_connector.get_work_items_batch(item_ids)
        return jsonify({
            'work_items': items,
            'configured': devops_connector.is_configured
        })
    except Exception as e:
        logger.error(f"Error getting linked DevOps work items for ticket {ticket_id}: {e}")
        return jsonify({'error': str(e), 'work_items': []}), 500


@devops_bp.route('/api/devops/projects')
@login_required
def get_devops_projects():
    """List all accessible Azure DevOps projects."""
    try:
        projects = devops_connector.list_projects()
        return jsonify({
            'projects': projects,
            'default_project': Config.AZURE_DEVOPS_PROJECT,
            'configured': devops_connector.is_configured
        })
    except Exception as e:
        logger.error(f"Error listing DevOps projects: {e}")
        return jsonify({'projects': [], 'configured': False}), 500


@devops_bp.route('/api/devops/meta')
@login_required
def get_devops_metadata():
    """Fetch metadata for default DevOps project."""
    try:
        meta = devops_connector.get_metadata()
        meta['configured'] = devops_connector.is_configured
        meta['project'] = devops_connector.project
        return jsonify(meta)
    except Exception as e:
        logger.error(f"Error getting DevOps metadata: {e}")
        return jsonify({
            'work_item_types': ['Bug', 'Task', 'User Story', 'Issue'],
            'area_paths': [],
            'iteration_paths': [],
            'users': [],
            'active_iteration': None,
            'configured': False
        })


@devops_bp.route('/api/devops/meta/<path:project_name>')
@login_required
def get_devops_metadata_for_project(project_name):
    """Fetch metadata (types, area paths, iteration paths, users) for a specific project."""
    try:
        meta = devops_connector.get_metadata_for_project(project_name)
        meta['configured'] = devops_connector.is_configured
        meta['project'] = project_name
        return jsonify(meta)
    except Exception as e:
        logger.error(f"Error getting DevOps metadata for project {project_name}: {e}")
        return jsonify({
            'work_item_types': ['Bug', 'Task', 'User Story', 'Issue'],
            'area_paths': [],
            'iteration_paths': [],
            'users': [],
            'active_iteration': None,
            'configured': False
        }), 500


@devops_bp.route('/api/devops/upload_attachment', methods=['POST'])
@login_required
def upload_devops_attachment():
    """
    Stream and upload media/files (screenshots, videos, logs) directly to Azure DevOps.
    Zero persistent disk usage, non-blocking streaming.
    """
    try:
        project = request.form.get('project') or Config.AZURE_DEVOPS_PROJECT
        file = request.files.get('file') or request.files.get('media')
        
        if not file:
            data = request.get_json(silent=True) or {}
            base64_str = data.get('image_base64') or data.get('data')
            filename = data.get('filename') or 'screenshot.png'
            project = data.get('project') or project
            if base64_str:
                match = re.match(r'^data:([^;]+);base64,(.+)$', base64_str)
                if match:
                    raw_bytes = base64.b64decode(match.group(2))
                    res = devops_connector.upload_attachment(project, raw_bytes, filename)
                    if res:
                        return jsonify({'success': True, **res})
                    return jsonify({'error': 'Failed to upload attachment to Azure DevOps'}), 500
            return jsonify({'error': 'No file uploaded'}), 400

        filename = secure_filename(file.filename) or 'attachment.png'
        file_bytes = file.read()
        res = devops_connector.upload_attachment(project, file_bytes, filename)
        if res:
            return jsonify({'success': True, **res})
        else:
            return jsonify({'error': 'Azure DevOps attachment upload failed'}), 500
    except Exception as e:
        logger.error(f"Error uploading DevOps attachment: {e}")
        return jsonify({'error': str(e)}), 500


@devops_bp.route('/api/devops/create', methods=['POST'])
@login_required
def create_devops_work_item():
    """Create a new work item in Azure DevOps and link it to the ticket."""
    try:
        data = request.get_json() or {}
        ticket_id = data.get('ticket_id')
        work_item_type = data.get('type') or 'Bug'
        title = data.get('title')
        target_project = data.get('project') or Config.AZURE_DEVOPS_PROJECT
        attachment_urls = data.get('attachment_urls') or []

        if not ticket_id or not title:
            return jsonify({'error': 'ticket_id and title are required'}), 400

        fields = {
            'System.Title': title,
            'System.Description': data.get('description', ''),
            'System.AssignedTo': data.get('assigned_to', ''),
            'System.AreaPath': data.get('area_path', ''),
            'System.IterationPath': data.get('iteration_path', ''),
            'System.Tags': data.get('tags', '')
        }

        if data.get('priority'):
            try:
                fields['Microsoft.VSTS.Common.Priority'] = int(data.get('priority'))
            except (ValueError, TypeError):
                pass

        if work_item_type.lower() == 'bug':
            if data.get('severity'):
                fields['Microsoft.VSTS.Common.Severity'] = data.get('severity')
            if data.get('repro_steps'):
                fields['Microsoft.VSTS.TCM.ReproSteps'] = data.get('repro_steps')
            if data.get('system_info'):
                fields['Microsoft.VSTS.TCM.SystemInfo'] = data.get('system_info')

        if target_project and target_project != devops_connector.project:
            orig_proj = devops_connector.project
            try:
                devops_connector.project = target_project
                created_item = devops_connector.create_work_item(work_item_type, fields, attachment_urls=attachment_urls)
            finally:
                devops_connector.project = orig_proj
        else:
            created_item = devops_connector.create_work_item(work_item_type, fields, attachment_urls=attachment_urls)

        if not created_item:
            return jsonify({'error': 'Failed to create work item in Azure DevOps'}), 500

        actor_username = session.get('user', {}).get('username', 'system')
        sql_logger.link_devops_work_item(ticket_id, created_item['id'], actor=actor_username, project=target_project)

        sio = get_socketio()
        if sio:
            sio.emit('ticket_updated', {'ticket_id': ticket_id, 'updated_by': session.get('user_id')})
        return jsonify({'success': True, 'work_item': created_item})
    except Exception as e:
        logger.error(f"Error creating DevOps work item: {e}")
        return jsonify({'error': str(e)}), 500


@devops_bp.route('/api/devops/users')
def search_devops_users():
    """Search Azure DevOps organization users dynamically with typeahead."""
    query = request.args.get('q', '')
    users = devops_connector.search_users(query=query)
    return jsonify({'users': users, 'configured': devops_connector.is_configured})


@devops_bp.route('/api/devops/link', methods=['POST'])
@login_required
def link_existing_devops_item():
    """Link an existing Azure DevOps work item by ID to a ticket across any project."""
    try:
        data = request.get_json() or {}
        ticket_id = data.get('ticket_id')
        raw_id = data.get('work_item_id')
        target_project = data.get('project') or Config.AZURE_DEVOPS_PROJECT

        if not ticket_id or not raw_id:
            return jsonify({'error': 'ticket_id and work_item_id are required'}), 400

        id_match = re.search(r'\d+', str(raw_id))
        if not id_match:
            return jsonify({'error': 'Invalid work item ID. Must contain numeric digits (e.g. 4097, BUG 4097)'}), 400
        work_item_id = int(id_match.group(0))

        item = devops_connector.get_work_item(work_item_id)
        if not item and devops_connector.is_configured:
            return jsonify({'error': f'Work Item #{work_item_id} not found in Azure DevOps'}), 404

        actual_project = (item.get('area_path') or '').split('\\')[0] or target_project
        actor_username = session.get('user', {}).get('username', 'system')
        success = sql_logger.link_devops_work_item(ticket_id, work_item_id, actor=actor_username, project=actual_project)

        if success:
            sio = get_socketio()
            if sio:
                sio.emit('ticket_updated', {'ticket_id': ticket_id, 'updated_by': session.get('user_id')})
            return jsonify({'success': True, 'work_item': item or {'id': work_item_id, 'title': f'Work Item #{work_item_id}'}})
        else:
            return jsonify({'error': 'Failed to link work item to ticket'}), 500
    except Exception as e:
        logger.error(f"Error linking DevOps item: {e}")
        return jsonify({'error': str(e)}), 500


@devops_bp.route('/api/devops/unlink', methods=['POST'])
@login_required
def unlink_devops_item():
    """Unlink an Azure DevOps work item from a ticket."""
    try:
        data = request.get_json() or {}
        ticket_id = data.get('ticket_id')
        raw_id = data.get('work_item_id')

        if not ticket_id or not raw_id:
            return jsonify({'error': 'ticket_id and work_item_id are required'}), 400

        work_item_id = int(str(raw_id).strip('#'))
        actor_username = session.get('user', {}).get('username', 'system')
        success = sql_logger.unlink_devops_work_item(ticket_id, work_item_id, actor=actor_username)

        if success:
            sio = get_socketio()
            if sio:
                sio.emit('ticket_updated', {'ticket_id': ticket_id, 'updated_by': session.get('user_id')})
            return jsonify({'success': True})
        else:
            return jsonify({'error': 'Failed to unlink work item'}), 500
    except Exception as e:
        logger.error(f"Error unlinking DevOps item: {e}")
        return jsonify({'error': str(e)}), 500

__all__ = ['devops_bp']
# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - Azure DevOps Blueprint - end
