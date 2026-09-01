# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - Refactor dashboard_socketio into Clean Blueprint & SocketIO App Factory - start
"""
dashboard_socketio.py

Flask + Flask-SocketIO Application Factory and Main Entry Point.
HTTP route endpoints are modularized in the routes/ package (Blueprints).
Real-time SocketIO event handlers are modularized in socketio_handlers.py.
Shared singletons and services reside in dashboard_context.py.
"""

import os
import logging
from datetime import datetime, date
from flask import Flask
from flask_socketio import SocketIO
from flask.json.provider import DefaultJSONProvider

from config import Config
from auth import init_auth
import dashboard_context
from dashboard_context import set_socketio, start_devops_poll_daemon
from routes import (
    auth_bp, core_bp, ticket_bp, attachment_bp, devops_bp, admin_config_bp
)
import socketio_handlers

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class CustomJSONProvider(DefaultJSONProvider):
    """Custom JSON serializer supporting ISO formatting for datetime and date instances."""
    def default(self, obj):
        """Serialize datetime and date objects to standard ISO strings."""
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)


# Bilal Khan (01/09/2026) Issue No  Sheet_Name  - App Factory Integration - start
from app import create_app

app, socketio = create_app()
# Bilal Khan (01/09/2026) Issue No  Sheet_Name  - App Factory Integration - end


if __name__ == '__main__':
    try:
        print("=" * 80)
        print("Starting Multi-User Dashboard with Custom Authentication")
        print("=" * 80)
        print(f"Dashboard URL: http://0.0.0.0:5000")
        print(f"Database: PostgreSQL ({Config.POSTGRES_HOST}:{Config.POSTGRES_PORT}/{Config.POSTGRES_DB})")
        print("=" * 80)
        
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

__all__ = ['app', 'socketio']
# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - Refactor dashboard_socketio into Clean Blueprint & SocketIO App Factory - end