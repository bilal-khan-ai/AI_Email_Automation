"""
PostgreSQL Connection Management and Shared Action/Actor Enums.
Provides thread-safe connection pooling via psycopg2.pool.ThreadedConnectionPool.
"""

# Bilal Khan (31/08/2026) Issue No  Sheet_Name  - PostgreSQL connection manager and DB enums - start
import psycopg2
from psycopg2 import pool
from contextlib import contextmanager
import threading
import logging
from config import Config

logger = logging.getLogger(__name__)


class ActionType:
    """Action type constants for ticket audit logs and lifecycle events."""
    TICKET_CREATED = "TICKET_CREATED"
    MESSAGE_RECEIVED = "MESSAGE_RECEIVED"
    MESSAGE_SENT = "MESSAGE_SENT"
    NOTE_ADDED = "NOTE_ADDED"
    STATUS_CHANGED = "STATUS_CHANGED"
    ASSIGNMENT_CHANGED = "ASSIGNMENT_CHANGED"
    AI_DRAFT_GENERATED = "AI_DRAFT_GENERATED"
    AI_DRAFT_UPDATED = "AI_DRAFT_UPDATED"
    TICKET_DELETED = "TICKET_DELETED"
    TICKET_RESTORED = "TICKET_RESTORED"
    # Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Add DevOps ActionTypes - start
    DEVOPS_ITEM_LINKED = "DEVOPS_ITEM_LINKED"
    DEVOPS_ITEM_UNLINKED = "DEVOPS_ITEM_UNLINKED"
    # Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Add DevOps ActionTypes - end


class ActorType:
    """Actor categories responsible for ticket lifecycle events."""
    SYSTEM = "SYSTEM"
    USER = "USER"
    AI = "AI"


class PostgreSQLConnectionManager:
    """
    Thread-safe connection pool manager for PostgreSQL.
    
    Uses psycopg2.pool.ThreadedConnectionPool for efficient connection reuse.
    Gracefully handles connection recycling and fallback for broken handles.
    """
    
    def __init__(self, min_conn: int = 1, max_conn: int = 10):
        """
        Initialize PostgreSQL connection pool parameters and thread-safe lock.
        
        Args:
            min_conn: Minimum number of pooled persistent connections.
            max_conn: Maximum number of active pooled connections.
        """
        self.host = getattr(Config, 'POSTGRES_HOST', 'localhost')
        self.port = getattr(Config, 'POSTGRES_PORT', '5432')
        self.database = getattr(Config, 'POSTGRES_DB', 'support_tickets')
        self.user = getattr(Config, 'POSTGRES_USER', 'postgres')
        self.password = getattr(Config, 'POSTGRES_PASSWORD', '')
        
        self.min_conn = getattr(Config, 'POSTGRES_MIN_CONN', min_conn)
        self.max_conn = getattr(Config, 'POSTGRES_MAX_CONN', max_conn)
        
        self.pool = None
        self.lock = threading.Lock()
        self._initialize_pool()
    
    def _initialize_pool(self):
        """Initialize connection pool"""
        try:
            self.pool = psycopg2.pool.ThreadedConnectionPool(
                self.min_conn,
                self.max_conn,
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                connect_timeout=10,
                options='-c statement_timeout=30000'
            )
            logger.info(f"✅ PostgreSQL connection pool initialized: {self.host}:{self.port}/{self.database}")
        except psycopg2.Error as e:
            logger.error(f"❌ PostgreSQL connection error: {e}")
            logger.error(f"   Connection parameters: Host={self.host}, Port={self.port}, DB={self.database}, User={self.user}")
            self.pool = None
        except Exception as e:
            logger.error(f"❌ Unexpected error initializing connection pool: {e}")
            self.pool = None
    
    @contextmanager
    def get_connection(self):
        """Context manager for getting a connection from the pool"""
        conn = None
        is_broken = False
        try:
            if self.pool and not self.pool.closed:
                conn = self.pool.getconn()
            else:
                conn = psycopg2.connect(
                    host=self.host,
                    port=self.port,
                    database=self.database,
                    user=self.user,
                    password=self.password,
                    connect_timeout=10,
                    options='-c statement_timeout=30000'
                )
            
            # Test connection health
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                
            yield conn
            
            # Commit if no exception occurred
            if conn and not conn.closed:
                conn.commit()
                
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            is_broken = True
            logger.error(f"Database connection error: {e}")
            if conn and not conn.closed:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise
        except Exception as e:
            logger.error(f"Database operation error: {e}")
            if conn and not conn.closed:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise
        finally:
            if conn and not conn.closed:
                if self.pool and not self.pool.closed:
                    try:
                        self.pool.putconn(conn, close=is_broken)
                    except Exception as e:
                        logger.error(f"Error returning connection to pool: {e}")
                        try:
                            conn.close()
                        except Exception:
                            pass
                else:
                    try:
                        conn.close()
                    except Exception:
                        pass
    
    def close_all(self):
        """Close all connections in the pool"""
        if self.pool and not self.pool.closed:
            self.pool.closeall()
            logger.info("Closed all PostgreSQL connections in pool")
# Bilal Khan (31/08/2026) Issue No  Sheet_Name  - PostgreSQL connection manager and DB enums - end
