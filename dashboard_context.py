# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - Shared Dashboard Context & Singletons - start
"""
dashboard_context.py

Shared application context: singletons, lazy helpers, and utility functions.
All blueprint route modules and socketio_handlers import from this module
to avoid circular dependencies with dashboard_socketio.py.
"""

import os
import io
import time
import logging
import threading
from typing import Dict, List, Optional, Any
from datetime import datetime, date

from config import Config
from modules.sql_logger import SQLLogger
from modules.env_manager import EnvManager
from modules.graph_connector import GraphConnector
from modules.devops_connector import AzureDevOpsConnector

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# Infrastructure Singletons & Paths
# ----------------------------------------------------------------------------
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
INLINE_UPLOAD_FOLDER = os.path.join(UPLOAD_FOLDER, 'inline')
os.makedirs(INLINE_UPLOAD_FOLDER, exist_ok=True)

ATTACHMENT_HASH_MAP: Dict[str, str] = {}
connected_users: Dict[str, Any] = {}
user_locks: Dict[str, Any] = {}

# ----------------------------------------------------------------------------
# Service Singletons
# ----------------------------------------------------------------------------
sql_logger = SQLLogger("postgres")
sql_logger.authenticate()

env_manager = EnvManager()

logger.info("Initializing global Graph connector...")
graph_connector = GraphConnector(
    Config.AZURE_CLIENT_ID,
    Config.AZURE_CLIENT_SECRET,
    Config.AZURE_TENANT_ID
)

if not graph_connector.authenticate():
    logger.warning("WARNING: Failed to authenticate with Microsoft Graph at startup. Attachment downloads and email sending may fail.")
else:
    logger.info("SUCCESS: Global Graph connector authenticated successfully")

devops_connector = AzureDevOpsConnector()

# ----------------------------------------------------------------------------
# SocketIO Instance Holder (eliminates circular import between blueprints & entrypoint)
# ----------------------------------------------------------------------------
_socketio_instance = None

def set_socketio(sio) -> None:
    """Store the initialized SocketIO instance for global access across blueprints."""
    global _socketio_instance
    _socketio_instance = sio

def get_socketio():
    """Retrieve the global SocketIO instance."""
    return _socketio_instance

# ----------------------------------------------------------------------------
# Thread-safe Lazy AI & Vector DB Initializers
# ----------------------------------------------------------------------------
ai_agent = None
_ai_agent_lock = threading.Lock()

def get_ai_agent():
    """Return OpenAIAgent instance or None if AI is disabled."""
    global ai_agent
    if not Config.ENABLE_AI or not Config.OPENAI_API_KEY:
        return None
    if ai_agent is None:
        with _ai_agent_lock:
            if ai_agent is None:
                try:
                    from modules.openai_agent import OpenAIAgent
                    ai_agent = OpenAIAgent(Config.OPENAI_API_KEY)
                    ai_agent.authenticate()
                except Exception as e:
                    logger.error(f"Failed to initialize lazy OpenAIAgent: {e}")
    return ai_agent

if Config.ENABLE_AI and Config.OPENAI_API_KEY:
    get_ai_agent()

_experience_db = None
_documentation_db = None
_db_lock = threading.Lock()

def get_experience_db():
    """Return the experience VectorDatabase, or None if RAG is disabled."""
    global _experience_db
    if not Config.ENABLE_RAG:
        return None
    if _experience_db is None:
        with _db_lock:
            if _experience_db is None:
                logger.info("Initializing lazy experience vector DB...")
                from modules.vector_db import VectorDatabase
                _experience_db = VectorDatabase(Config.CHROMA_DB_PATH, Config.COLLECTION_NAME, force_cpu=True)
    return _experience_db

def get_documentation_db():
    """Return the documentation BookVectorDB, or None if RAG is disabled."""
    global _documentation_db
    if not Config.ENABLE_RAG:
        return None
    if _documentation_db is None:
        with _db_lock:
            if _documentation_db is None:
                logger.info("Initializing lazy documentation vector DB...")
                from modules.vector_db import BookVectorDB
                _documentation_db = BookVectorDB(Config.BOOKSTACK_DB_PATH)
    return _documentation_db

# ----------------------------------------------------------------------------
# Shared Domain Utility Functions
# ----------------------------------------------------------------------------
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
    if status_lower in ['resolved', 'completed', 'closed']:
        return 'Closed'
    elif status_lower in ['review', 'pending review']:
        return 'Review'
    elif status_lower in ['ignore']:
        return 'Ignore'
    elif status_lower in ['pending', 'open', 'in progress']:
        return 'Open'
    else:
        return 'Open'


def calculate_stats(tickets: List[Dict[str, Any]], user: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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


def extract_inline_images(html_content: str) -> tuple:
    """
    Extract embedded base64 images from HTML and replace them with CID references.
    
    Returns:
        tuple: (modified_html, inline_attachments_list)
    """
    if not html_content:
        return html_content, []
    
    from bs4 import BeautifulSoup
    import re
    import uuid
    
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
                'cid': cid,
                'content_type': content_type,
                'content': base64_data,
                'is_inline': True
            })
            
    return str(soup), inline_attachments


# ----------------------------------------------------------------------------
# DevOps 10-Minute Background State Polling Daemon
# ----------------------------------------------------------------------------
_devops_poll_state_cache: Dict[int, str] = {}
_devops_poll_daemon_started = False

def _devops_poll_daemon():
    """
    Background worker: Every 10 minutes, checks live state of all linked DevOps items.
    Runs as an Eventlet greenlet background task (zero OS thread overhead on 1GB VM).
    Pushes state changes to open dashboard sessions via SocketIO.
    """
    from collections import defaultdict

    POLL_INTERVAL_SECONDS = 600  # 10 minutes
    sio = get_socketio()

    logger.info("🔄 DevOps state polling daemon started (interval: 600s / 10min)")
    while True:
        try:
            if sio:
                sio.sleep(POLL_INTERVAL_SECONDS)
            else:
                time.sleep(POLL_INTERVAL_SECONDS)

            if devops_connector.is_configured:
                all_linked = sql_logger.get_all_linked_devops_work_items()
                if all_linked:
                    by_project = defaultdict(list)
                    item_to_ticket = {}
                    for row in all_linked:
                        proj = row.get('project') or Config.AZURE_DEVOPS_PROJECT
                        wid = row['work_item_id']
                        by_project[proj].append(wid)
                        item_to_ticket[wid] = row['ticket_id']

                    for proj, ids in by_project.items():
                        orig_proj = devops_connector.project
                        try:
                            if proj:
                                devops_connector.project = proj
                            fresh_items = devops_connector.get_work_items_batch(ids)
                        finally:
                            devops_connector.project = orig_proj

                        for item in fresh_items:
                            wid = item['id']
                            new_state = item.get('state', '')
                            old_state = _devops_poll_state_cache.get(wid)

                            if old_state is not None and old_state != new_state:
                                ticket_id = item_to_ticket.get(wid)
                                logger.info(f"📡 DevOps WI#{wid} state changed: {old_state} -> {new_state} (Ticket: {ticket_id})")
                                if sio:
                                    sio.emit('devops_item_updated', {
                                        'ticket_id': ticket_id,
                                        'work_item_id': wid,
                                        'old_state': old_state,
                                        'new_state': new_state,
                                        'item': item
                                    })

                            _devops_poll_state_cache[wid] = new_state
        except Exception as e:
            logger.error(f"❌ DevOps state polling daemon error: {e}")


def start_devops_poll_daemon(sio=None):
    """Start the background DevOps polling daemon using SocketIO's async task runner."""
    global _devops_poll_daemon_started
    sio = sio or get_socketio()
    if not _devops_poll_daemon_started and sio:
        _devops_poll_daemon_started = True
        sio.start_background_task(_devops_poll_daemon)
        logger.info("✅ DevOps state polling greenlet background task initialized")

__all__ = [
    'DATA_DIR',
    'UPLOAD_FOLDER',
    'INLINE_UPLOAD_FOLDER',
    'ATTACHMENT_HASH_MAP',
    'connected_users',
    'user_locks',
    'sql_logger',
    'env_manager',
    'graph_connector',
    'devops_connector',
    'set_socketio',
    'get_socketio',
    'get_ai_agent',
    'get_experience_db',
    'get_documentation_db',
    'normalize_legacy_status',
    'calculate_stats',
    'extract_inline_images',
    '_devops_poll_daemon',
    'start_devops_poll_daemon',
]
# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - Shared Dashboard Context & Singletons - end
