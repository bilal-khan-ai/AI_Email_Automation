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


# ----------------------------------------------------------------------------
# Application & SocketIO Factory
# ----------------------------------------------------------------------------
app = Flask(__name__, template_folder='Pages')
app.json = CustomJSONProvider(app)
app.config['SECRET_KEY'] = Config.SECRET_KEY

# Initialize custom authentication system
init_auth(app)

# Initialize SocketIO with eventlet async mode and configured CORS
socketio = SocketIO(
    app,
    cors_allowed_origins=Config.CORS_ALLOWED_ORIGINS,
    async_mode='eventlet',
    json=app.json,
    logger=True,
    engineio_logger=False
)

# Store SocketIO in context to enable cross-module emissions without circular imports
set_socketio(socketio)

# ----------------------------------------------------------------------------
# Register Modular Flask Blueprints
# ----------------------------------------------------------------------------
app.register_blueprint(core_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(ticket_bp)
app.register_blueprint(attachment_bp)
app.register_blueprint(devops_bp)
app.register_blueprint(admin_config_bp)

# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - Legacy Endpoint URL Fallback Handler - start
def _handle_url_build_error(error, endpoint, values):
    """Fallback handler resolving legacy flat endpoint names to their registered blueprints."""
    from flask import url_for as _flask_url_for
    for bp_name in app.blueprints:
        candidate = f"{bp_name}.{endpoint}"
        if candidate in app.view_functions:
            return _flask_url_for(candidate, **values)
    raise error

app.url_build_error_handlers.append(_handle_url_build_error)
# Bilal Khan (31/08/2026) Issue No 14 Sheet_Name  - Legacy Endpoint URL Fallback Handler - end

# ----------------------------------------------------------------------------
# Register SocketIO Event Listeners
# ----------------------------------------------------------------------------
socketio_handlers.register_handlers(socketio)

# ----------------------------------------------------------------------------
# Start Background Daemons
# ----------------------------------------------------------------------------
start_devops_poll_daemon(socketio)


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