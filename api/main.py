import os
from flask import Flask, jsonify, session
from flask_cors import CORS
from api.models.sql_logger import SQLLogger
from api.core.agent import SupportAgent
from config import Config
from api.auth import init_auth, get_current_user, is_admin

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = Config.SECRET_KEY
    
    # Enable CORS for React development
    CORS(app, supports_credentials=True, origins=[Config.REACT_APP_URL if hasattr(Config, 'REACT_APP_URL') else "http://localhost:5173"])
    
    init_auth(app)
    
    # Initialize SQL Logger
    sql_logger = SQLLogger("postgres")
    sql_logger.authenticate()
    
    # Initialize Support Agent (shared or on-demand)
    # Note: In a REST API, we might want to share some state or just use transient agents.
    # For now, we'll keep it simple.
    
    # Register Blueprints
    from api.routes.tickets import tickets_bp
    from api.routes.actions import actions_bp
    app.register_blueprint(tickets_bp)
    app.register_blueprint(actions_bp)

    @app.route('/api/auth/me')
    def auth_me():
        """Check current session and return user info"""
        user = get_current_user()
        if user:
            return jsonify({
                'authenticated': True,
                'user': user,
                'is_admin': is_admin()
            })
        return jsonify({'authenticated': False}), 401

    # Placeholder for other routes - will be moved to separate files
    @app.route('/api/stats')
    def get_stats():
        tickets = sql_logger.get_active_tickets()
        total = len(tickets)
        open_count = sum(1 for t in tickets if t.get('status', 'Open').lower() == 'open')
        return jsonify({
            'total': total,
            'open': open_count,
            'closed': total - open_count
        })

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)
