# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - Blueprint Aggregator - start
"""
routes/__init__.py

Aggregates and exposes all Flask Blueprints for clean registration in dashboard_socketio.py.
"""

from .auth_routes import auth_bp
from .core_routes import core_bp
from .ticket_routes import ticket_bp
from .attachment_routes import attachment_bp
from .devops_routes import devops_bp
from .admin_config_routes import admin_config_bp

__all__ = [
    'auth_bp',
    'core_bp',
    'ticket_bp',
    'attachment_bp',
    'devops_bp',
    'admin_config_bp',
]
# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - Blueprint Aggregator - end
