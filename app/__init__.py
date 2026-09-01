# Bilal Khan (01/09/2026) Issue No  Sheet_Name  - App Factory and Package Initialization - start
"""
app/__init__.py
Core Flask & SocketIO application factory.
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


class CustomJSONProvider(DefaultJSONProvider):
    """Custom JSON serializer supporting ISO formatting for datetime and date instances."""
    def default(self, obj):
        """Serialize datetime and date objects to standard ISO strings."""
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)


def create_app():
    """Application factory for Flask + SocketIO."""
    template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'templates'))
    static_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'static'))
    
    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
    app.json = CustomJSONProvider(app)
    app.config['SECRET_KEY'] = Config.SECRET_KEY

    # Initialize authentication system
    init_auth(app)

    # Initialize SocketIO with eventlet
    socketio = SocketIO(
        app,
        cors_allowed_origins=Config.CORS_ALLOWED_ORIGINS,
        async_mode='eventlet',
        json=app.json,
        logger=False,
        engineio_logger=False
    )

    # Store SocketIO in context
    set_socketio(socketio)

    # Register Blueprints
    app.register_blueprint(core_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(ticket_bp)
    app.register_blueprint(attachment_bp)
    app.register_blueprint(devops_bp)
    app.register_blueprint(admin_config_bp)

    # Legacy endpoint URL fallback handler
    def _handle_url_build_error(error, endpoint, values):
        from flask import url_for as _flask_url_for
        for bp_name in app.blueprints:
            candidate = f"{bp_name}.{endpoint}"
            if candidate in app.view_functions:
                return _flask_url_for(candidate, **values)
        raise error

    app.url_build_error_handlers.append(_handle_url_build_error)

    # Register SocketIO Event Listeners
    socketio_handlers.register_handlers(socketio)

    # Start Background Daemons
    start_devops_poll_daemon(socketio)

    return app, socketio
# Bilal Khan (01/09/2026) Issue No  Sheet_Name  - App Factory and Package Initialization - end
