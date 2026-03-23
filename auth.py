"""
Custom Authentication Module
Simple username/password authentication with role-based access control
"""

import hashlib
import secrets
from flask import session, redirect, url_for, request, flash
from functools import wraps
from datetime import datetime
import logging
import psycopg2
from psycopg2 import pool
from config import Config

logger = logging.getLogger(__name__)

# ===============================
# GLOBAL USER MANAGER (CRITICAL)
# ===============================
user_manager = None


class UserManager:
    """Handle user authentication and management"""
    
    def __init__(self):
        """Initialize user manager with PostgreSQL backend"""
        try:
            # Create connection pool
            self.pool = psycopg2.pool.ThreadedConnectionPool(
                1, 10,
                host=Config.POSTGRES_HOST,
                port=Config.POSTGRES_PORT,
                database=Config.POSTGRES_DB,
                user=Config.POSTGRES_USER,
                password=Config.POSTGRES_PASSWORD
            )
            
            # Initialize users table
            self._init_users_table()
            
            logger.info("✅ User Manager initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize User Manager: {e}")
            raise
    
    def _init_users_table(self):
        """Create users table if it doesn't exist"""
        conn = None
        try:
            conn = self.pool.getconn()
            cursor = conn.cursor()
            
            # Create users table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(100) UNIQUE NOT NULL,
                    password_hash VARCHAR(128) NOT NULL,
                    role VARCHAR(20) NOT NULL DEFAULT 'user',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP,
                    is_active BOOLEAN DEFAULT TRUE,
                    CHECK (role IN ('admin', 'user'))
                )
            """)
            
            # Create index on username for faster lookups
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_username 
                ON users(username)
            """)
            
            conn.commit()
            logger.info("✅ Users table initialized")
            
            # Create default admin user if no users exist
            cursor.execute("SELECT COUNT(*) FROM users")
            count = cursor.fetchone()[0]
            
            if count == 0:
                # Create default admin (password: admin123 - CHANGE THIS!)
                self._create_default_admin(cursor, conn)
            
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"❌ Failed to initialize users table: {e}")
            raise
        finally:
            if conn:
                self.pool.putconn(conn)
    
    def _create_default_admin(self, cursor, conn):
        """Create default admin user"""
        try:
            default_password = "admin123"  # CHANGE THIS IMMEDIATELY!
            password_hash = self._hash_password(default_password)
            
            cursor.execute("""
                INSERT INTO users (username, password_hash, role)
                VALUES (%s, %s, %s)
            """, ("admin", password_hash, "admin"))
            
            conn.commit()
            
            logger.warning(
                "⚠️  DEFAULT ADMIN CREATED | "
                "Username: 'admin' | Password: 'admin123' | "
                "CHANGE PASSWORD IMMEDIATELY!"
            )
        except Exception as e:
            logger.error(f"❌ Failed to create default admin: {e}")
    
    def _hash_password(self, password: str) -> str:
        """
        Hash password using SHA-256 with salt.
        
        Format: salt$hash
        """
        # Generate random salt
        salt = secrets.token_hex(16)
        
        # Hash password with salt
        password_with_salt = f"{salt}{password}"
        hash_obj = hashlib.sha256(password_with_salt.encode())
        password_hash = hash_obj.hexdigest()
        
        # Return salt$hash format
        return f"{salt}${password_hash}"
    
    def _verify_password(self, password: str, stored_hash: str) -> bool:
        """
        Verify password against stored hash.
        
        Args:
            password: Plain text password
            stored_hash: Stored hash in salt$hash format
            
        Returns:
            True if password matches, False otherwise
        """
        try:
            # Split salt and hash
            salt, hash_value = stored_hash.split('$')
            
            # Hash provided password with stored salt
            password_with_salt = f"{salt}{password}"
            hash_obj = hashlib.sha256(password_with_salt.encode())
            computed_hash = hash_obj.hexdigest()
            
            # Compare hashes
            return computed_hash == hash_value
        except Exception as e:
            logger.error(f"❌ Password verification failed: {e}")
            return False
    
    def authenticate_user(self, username: str, password: str) -> dict:
        """
        Authenticate user with username and password.
        
        Args:
            username: Username
            password: Plain text password
            
        Returns:
            User dict if authenticated, None otherwise
        """
        conn = None
        try:
            conn = self.pool.getconn()
            cursor = conn.cursor()
            
            # Fetch user
            cursor.execute("""
                SELECT id, username, password_hash, role, is_active
                FROM users
                WHERE username = %s
            """, (username,))
            
            user = cursor.fetchone()
            
            if not user:
                logger.warning(f"⚠️  Login attempt for non-existent user: {username}")
                return None
            
            user_id, username, password_hash, role, is_active = user
            
            # Check if user is active
            if not is_active:
                logger.warning(f"⚠️  Login attempt for disabled user: {username}")
                return None
            
            # Verify password
            if not self._verify_password(password, password_hash):
                logger.warning(f"⚠️  Failed login attempt for user: {username}")
                return None
            
            # Update last login
            cursor.execute("""
                UPDATE users
                SET last_login = %s
                WHERE id = %s
            """, (datetime.now(), user_id))
            
            conn.commit()
            
            logger.info(f"✅ User authenticated: {username} (Role: {role})")
            
            return {
                'id': user_id,
                'username': username,
                'role': role
            }
        
        except Exception as e:
            logger.error(f"❌ Authentication error: {e}")
            return None
        finally:
            if conn:
                self.pool.putconn(conn)
    
    def create_user(self, username: str, password: str, role: str = 'user') -> bool:
        """
        Create a new user.
        
        Args:
            username: Username (must be unique)
            password: Plain text password (will be hashed)
            role: User role ('admin' or 'user')
            
        Returns:
            True if user created, False otherwise
        """
        conn = None
        try:
            # Validate role
            if role not in ['admin', 'user']:
                logger.error(f"❌ Invalid role: {role}")
                return False
            
            # Hash password
            password_hash = self._hash_password(password)
            
            conn = self.pool.getconn()
            cursor = conn.cursor()
            
            # Create user
            cursor.execute("""
                INSERT INTO users (username, password_hash, role)
                VALUES (%s, %s, %s)
            """, (username, password_hash, role))
            
            conn.commit()
            
            logger.info(f"✅ User created: {username} (Role: {role})")
            return True
        
        except psycopg2.IntegrityError:
            if conn:
                conn.rollback()
            logger.error(f"❌ User already exists: {username}")
            return False
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"❌ Failed to create user: {e}")
            return False
        finally:
            if conn:
                self.pool.putconn(conn)
    
    def update_password(self, username: str, new_password: str) -> bool:
        """
        Update user password.
        
        Args:
            username: Username
            new_password: New plain text password (will be hashed)
            
        Returns:
            True if password updated, False otherwise
        """
        conn = None
        try:
            # Hash new password
            password_hash = self._hash_password(new_password)
            
            conn = self.pool.getconn()
            cursor = conn.cursor()
            
            # Update password
            cursor.execute("""
                UPDATE users
                SET password_hash = %s
                WHERE username = %s
            """, (password_hash, username))
            
            if cursor.rowcount == 0:
                logger.error(f"❌ User not found: {username}")
                return False
            
            conn.commit()
            
            logger.info(f"✅ Password updated for user: {username}")
            return True
        
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"❌ Failed to update password: {e}")
            return False
        finally:
            if conn:
                self.pool.putconn(conn)
    
    def delete_user(self, username: str) -> bool:
        """
        Delete a user (actually disables the user).
        
        Args:
            username: Username
            
        Returns:
            True if user disabled, False otherwise
        """
        conn = None
        try:
            conn = self.pool.getconn()
            cursor = conn.cursor()
            
            # Disable user instead of deleting
            cursor.execute("""
                UPDATE users
                SET is_active = FALSE
                WHERE username = %s
            """, (username,))
            
            if cursor.rowcount == 0:
                logger.error(f"❌ User not found: {username}")
                return False
            
            conn.commit()
            
            logger.info(f"✅ User disabled: {username}")
            return True
        
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"❌ Failed to disable user: {e}")
            return False
        finally:
            if conn:
                self.pool.putconn(conn)
    
    def list_users(self) -> list:
        """
        List all active users.
        
        Returns:
            List of user dicts
        """
        conn = None
        try:
            conn = self.pool.getconn()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT id, username, role, created_at, last_login, is_active
                FROM users
                ORDER BY created_at DESC
            """)
            
            users = []
            for row in cursor.fetchall():
                users.append({
                    'id': row[0],
                    'username': row[1],
                    'role': row[2],
                    'created_at': row[3].isoformat() if row[3] else None,
                    'last_login': row[4].isoformat() if row[4] else None,
                    'is_active': row[5]
                })
            
            return users
        
        except Exception as e:
            logger.error(f"❌ Failed to list users: {e}")
            return []
        finally:
            if conn:
                self.pool.putconn(conn)
    
    def get_user(self, username: str) -> dict:
        """
        Get user by username.
        
        Args:
            username: Username
            
        Returns:
            User dict or None
        """
        conn = None
        try:
            conn = self.pool.getconn()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT id, username, role, created_at, last_login, is_active
                FROM users
                WHERE username = %s
            """, (username,))
            
            user = cursor.fetchone()
            
            if not user:
                return None
            
            return {
                'id': user[0],
                'username': user[1],
                'role': user[2],
                'created_at': user[3].isoformat() if user[3] else None,
                'last_login': user[4].isoformat() if user[4] else None,
                'is_active': user[5]
            }
        
        except Exception as e:
            logger.error(f"❌ Failed to get user: {e}")
            return None
        finally:
            if conn:
                self.pool.putconn(conn)
    
    def cleanup(self):
        """Close connection pool"""
        if self.pool:
            self.pool.closeall()
            logger.info("✅ User Manager connection pool closed")


def init_auth(app):
    """
    Initialize authentication for Flask app.
    
    Args:
        app: Flask app instance
    """
    global user_manager
    
    # Configure session
    app.config['SECRET_KEY'] = Config.SECRET_KEY
    app.config['SESSION_TYPE'] = 'filesystem'
    app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # 1 hour
    
    # Initialize user manager
    user_manager = UserManager()
    
    logger.info("✅ Authentication system initialized")


def login_required(f):
    """
    Decorator to require authentication for routes.
    
    Usage:
        @app.route('/dashboard')
        @login_required
        def dashboard():
            return render_template('dashboard.html')
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """
    Decorator to require admin role for routes.
    
    Usage:
        @app.route('/admin/users')
        @admin_required
        def manage_users():
            return render_template('users.html')
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login', next=request.url))
        
        if session['user'].get('role') != 'admin':
            flash('You do not have permission to access this page.', 'danger')
            return redirect(url_for('index'))
        
        return f(*args, **kwargs)
    return decorated_function


def socketio_login_required(f):
    """
    Decorator to require authentication for SocketIO events.
    
    Usage:
        @socketio.on('some_event')
        @socketio_login_required
        def handle_event(data):
            # Handle event
            pass
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return {'error': 'Unauthorized', 'message': 'Please log in first'}, 401
        return f(*args, **kwargs)
    return decorated_function


def socketio_admin_required(f):
    """
    Decorator to require admin role for SocketIO events.
    
    Usage:
        @socketio.on('admin_event')
        @socketio_admin_required
        def handle_admin_event(data):
            # Handle admin event
            pass
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return {'error': 'Unauthorized', 'message': 'Please log in first'}, 401
        
        if session['user'].get('role') != 'admin':
            return {'error': 'Forbidden', 'message': 'Admin access required'}, 403
        
        return f(*args, **kwargs)
    return decorated_function


def get_current_user():
    """
    Get current logged-in user from session.
    
    Returns:
        User dict or None
    """
    return session.get('user')


def is_admin():
    """
    Check if current user is admin.
    
    Returns:
        True if admin, False otherwise
    """
    user = get_current_user()
    return user and user.get('role') == 'admin'