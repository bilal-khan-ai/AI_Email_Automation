import psycopg2
from psycopg2 import pool, extras, sql
from psycopg2.extensions import ISOLATION_LEVEL_READ_COMMITTED
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Union
import os
from contextlib import contextmanager
import threading
from config import Config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] %(message)s'
)
logger = logging.getLogger(__name__)


class PostgreSQLConnectionManager:
    """
    Lightweight connection manager for PostgreSQL.
    
    Design notes:
    - Uses psycopg2.pool.ThreadedConnectionPool for thread-safe connection reuse
    - Pool size can be configured via environment variables
    - Graceful fallback to single connections if pooling fails
    """
    
    def __init__(self, min_conn=1, max_conn=10):
        self.host = getattr(Config, 'POSTGRES_HOST', 'localhost')
        self.port = getattr(Config,'POSTGRES_PORT', '5432')
        self.database = getattr(Config,'POSTGRES_DB', 'support_tickets')
        self.user = getattr(Config,'POSTGRES_USER', 'postgres')
        self.password = getattr(Config,'POSTGRES_PASSWORD', '')
        
        self.min_conn = getattr(Config,'POSTGRES_MIN_CONN', min_conn)
        self.max_conn = getattr(Config,'POSTGRES_MAX_CONN', max_conn)
        
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
            self.pool = None # Set pool to None to allow fallback to single connections
            # Do not re-raise here if the intention is to allow fallback
        except Exception as e:
            logger.error(f"❌ Unexpected error initializing connection pool: {e}")
            self.pool = None # Set pool to None to allow fallback to single connections
            # Do not re-raise here if the intention is to allow fallback
    
    @contextmanager
    def get_connection(self):
        """Context manager for getting a connection from the pool"""
        conn = None
        try:
            if self.pool:
                conn = self.pool.getconn()
            else:
                conn = psycopg2.connect(
                    host=self.host,
                    port=self.port,
                    database=self.database,
                    user=self.user,
                    password=self.password,
                    connect_timeout=10
                )
            
            conn.set_isolation_level(ISOLATION_LEVEL_READ_COMMITTED)
            yield conn
            conn.commit()
            
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"❌ Database error: {e}")
            raise
        finally:
            if conn:
                if self.pool:
                    self.pool.putconn(conn)
                else:
                    conn.close()
    
    def close_all(self):
        """Close all connections in the pool"""
        if self.pool:
            self.pool.closeall()
            logger.info("🔒 Connection pool closed")


class SQLLogger:
    """
    PostgreSQL-based ticket and message logger with enhanced observability.
    
    Improvements:
    - Structured logging with ticket_id and message_id context
    - Provenance storage for AI responses (RAG docs + similarity scores)
    - Soft-delete mechanism for vector DB hygiene
    - Better error handling and recovery
    """
    
    def __init__(self, db_file: str = "data/support_tickets.db"):
        """
        Initialize SQL Logger with PostgreSQL backend.
        
        Args:
            db_file: Ignored (kept for API compatibility)
        """
        self.db_file = db_file
        self.conn_manager = PostgreSQLConnectionManager()
        logger.info("✅ SQLLogger initialized with PostgreSQL backend")
    
    def authenticate(self):
        """
        Initialize tables with the relational schema + provenance storage.
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    # 1. PARENT TABLE: TICKETS
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS tickets (
                            id SERIAL PRIMARY KEY,
                            ticket_id TEXT UNIQUE NOT NULL,
                            conversation_id TEXT,
                            subject TEXT,
                            customer_email TEXT,
                            status TEXT DEFAULT 'Open',
                            assigned_to TEXT DEFAULT 'Unassigned',
                            ai_draft TEXT,
                            rag_provenance JSONB,
                            created_at TIMESTAMPTZ NOT NULL,
                            last_updated TIMESTAMPTZ NOT NULL,
                            deleted_at TIMESTAMPTZ,
                            reopened BOOLEAN DEFAULT FALSE
                        )
                    """)
                    
                    # Ensure reopened column exists (for existing databases)
                    cur.execute("""
                        DO $$ 
                        BEGIN 
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                           WHERE table_name='tickets' AND column_name='reopened') THEN 
                                ALTER TABLE tickets ADD COLUMN reopened BOOLEAN DEFAULT FALSE;
                            END IF;
                        END $$;
                    """)
                    
                    # 2. CHILD TABLE: MESSAGES
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS ticket_messages (
                            id SERIAL PRIMARY KEY,
                            ticket_id TEXT NOT NULL,
                            message_id TEXT UNIQUE NOT NULL,
                            sender TEXT,
                            body_text TEXT,
                            timestamp TIMESTAMPTZ,
                            attachments TEXT,
                            is_internal BOOLEAN DEFAULT FALSE,
                            deleted_at TIMESTAMPTZ,
                            FOREIGN KEY(ticket_id) REFERENCES tickets(ticket_id) ON DELETE CASCADE
                        )
                    """)
                    
                    # 3. INDEXES
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_conv_id 
                        ON tickets(conversation_id)
                    """)
                    
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_msg_ticket 
                        ON ticket_messages(ticket_id)
                    """)
                    
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_msg_timestamp 
                        ON ticket_messages(timestamp DESC)
                    """)
                    
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_ticket_status 
                        ON tickets(status)
                    """)
                    
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_ticket_updated 
                        ON tickets(last_updated DESC)
                    """)
                    
                    # New indexes for soft-delete support
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_ticket_deleted
                        ON tickets(deleted_at) WHERE deleted_at IS NOT NULL
                    """)
                    
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_msg_deleted
                        ON ticket_messages(deleted_at) WHERE deleted_at IS NOT NULL
                    """)
                    
            logger.info("✅ PostgreSQL schema initialized (with provenance + soft-delete support)")
            return True
            
        except Exception as e:
            logger.error(f"❌ Schema initialization failed: {e}")
            return False
    
    def create_ticket(self, ticket_id: str, conversation_id: str, subject: str, 
                     customer_email: str) -> bool:
        """
        Create a new ticket (idempotent).
        
        Args:
            ticket_id: Unique ticket identifier
            conversation_id: Graph conversation ID
            subject: Email subject
            customer_email: Customer's email address
            
        Returns:
            bool: True if created or already exists, False on error
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    now = datetime.now()
                    
                    cur.execute("""
                        INSERT INTO tickets (
                            ticket_id, conversation_id, subject, 
                            customer_email, created_at, last_updated
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (ticket_id) DO NOTHING
                    """, (ticket_id, conversation_id, subject, customer_email, now, now))
                    
                    created = cur.rowcount > 0
                    
                    if created:
                        logger.info(f"✅ Created ticket {ticket_id} | Customer: {customer_email} | Subject: {subject[:50]}")
                    else:
                        logger.debug(f"ℹ️  Ticket {ticket_id} already exists (idempotent)")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error creating ticket {ticket_id}: {e}")
            return False
    
    def log_message(self, ticket_id: str, email: dict, is_internal: bool = False) -> bool:
        """
        Log a message to a ticket (idempotent).
        
        Args:
            ticket_id: Ticket ID to add message to
            email: Email dict with id, sender, body, timestamp, attachments
            is_internal: Whether message is from support team
            
        Returns:
            bool: True if logged or already exists, False on error
        """
        try:
            message_id = email.get('id')
            
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO ticket_messages (
                            ticket_id, message_id, sender, body_text,
                            timestamp, attachments, is_internal
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (message_id) DO NOTHING
                    """, (
                        ticket_id,
                        message_id,
                        email.get('sender'),
                        email.get('body'),
                        email.get('received'),
                        json.dumps(email.get('attachments', [])),
                        is_internal
                    ))
                    
                    logged = cur.rowcount > 0
                    
                    # Update ticket's last_updated timestamp
                    cur.execute("""
                        UPDATE tickets 
                        SET last_updated = %s 
                        WHERE ticket_id = %s
                    """, (datetime.now(), ticket_id))
                    
                    if logged:
                        speaker = "INTERNAL" if is_internal else "CUSTOMER"
                        logger.info(
                            f"✅ Logged message {message_id[:20]}... to ticket {ticket_id} "
                            f"| Speaker: {speaker} | Sender: {email.get('sender', 'unknown')}"
                        )
                    else:
                        logger.debug(f"ℹ️  Message {message_id[:20]}... already exists (idempotent)")
            
            return True
            
        except Exception as e:
            logger.error(
                f"❌ Error logging message {email.get('id', 'unknown')[:20]}... "
                f"to ticket {ticket_id}: {e}"
            )
            return False
    
    def update_ai_draft(self, ticket_id: str, draft: str, provenance: Optional[List[Dict]] = None) -> bool:
        """
        Update AI draft for a ticket with optional RAG provenance.
        
        Provenance format:
        [
            {
                'rank': 1,
                'subject': 'Past ticket subject',
                'similarity_score': 0.87,
                'metadata': {...}
            },
            ...
        ]
        
        Args:
            ticket_id: Ticket ID to update
            draft: AI-generated draft response
            provenance: List of RAG docs used (with similarity scores)
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    if provenance is not None:
                        # Store provenance as JSONB
                        provenance_json = json.dumps(provenance)
                        
                        cur.execute("""
                            UPDATE tickets 
                            SET ai_draft = %s, 
                                rag_provenance = %s::jsonb,
                                last_updated = %s 
                            WHERE ticket_id = %s
                        """, (draft, provenance_json, datetime.now(), ticket_id))
                        
                        logger.info(
                            f"✅ Updated AI draft for ticket {ticket_id} "
                            f"| Provenance: {len(provenance)} RAG docs stored"
                        )
                    else:
                        cur.execute("""
                            UPDATE tickets 
                            SET ai_draft = %s, 
                                last_updated = %s 
                            WHERE ticket_id = %s
                        """, (draft, datetime.now(), ticket_id))
                        
                        logger.info(f"✅ Updated AI draft for ticket {ticket_id} (no provenance)")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error updating AI draft for ticket {ticket_id}: {e}")
            return False
    
    def soft_delete_ticket(self, ticket_id: str) -> bool:
        """
        Soft-delete a ticket (marks as deleted without removing from DB).
        
        This allows safe cleanup of vector DB entries later.
        
        Args:
            ticket_id: Ticket ID to soft-delete
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    now = datetime.now()
                    
                    cur.execute("""
                        UPDATE tickets 
                        SET deleted_at = %s, 
                            status = 'Deleted',
                            last_updated = %s
                        WHERE ticket_id = %s
                    """, (now, now, ticket_id))
                    
                    if cur.rowcount > 0:
                        logger.info(f"✅ Soft-deleted ticket {ticket_id}")
                        return True
                    else:
                        logger.warning(f"⚠️  Ticket {ticket_id} not found for soft-delete")
                        return False
            
        except Exception as e:
            logger.error(f"❌ Error soft-deleting ticket {ticket_id}: {e}")
            return False
    
    def reopen_ticket(self, ticket_id: str) -> bool:
        """
        Re-open a soft-deleted ticket.
        
        Args:
            ticket_id: Ticket ID to re-open
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    now = datetime.now()
                    
                    cur.execute("""
                        UPDATE tickets 
                        SET deleted_at = NULL, 
                            status = 'Open',
                            reopened = TRUE,
                            last_updated = %s
                        WHERE ticket_id = %s
                    """, (now, ticket_id))
                    
                    if cur.rowcount > 0:
                        logger.info(f"🔄 Re-opened ticket {ticket_id}")
                        return True
                    else:
                        logger.warning(f"⚠️  Ticket {ticket_id} not found for re-opening")
                        return False
            
        except Exception as e:
            logger.error(f"❌ Error re-opening ticket {ticket_id}: {e}")
            return False
    
    def soft_delete_message(self, message_id: str) -> bool:
        """
        Soft-delete a message (marks as deleted without removing from DB).
        
        Args:
            message_id: Message ID to soft-delete
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    now = datetime.now()
                    
                    cur.execute("""
                        UPDATE ticket_messages 
                        SET deleted_at = %s
                        WHERE message_id = %s
                    """, (now, message_id))
                    
                    if cur.rowcount > 0:
                        logger.info(f"✅ Soft-deleted message {message_id[:20]}...")
                        return True
                    else:
                        logger.warning(f"⚠️  Message {message_id[:20]}... not found for soft-delete")
                        return False
            
        except Exception as e:
            logger.error(f"❌ Error soft-deleting message {message_id[:20]}...: {e}")
            return False
    
    def get_deleted_tickets(self, since: Optional[datetime] = None) -> List[str]:
        """
        Get list of deleted ticket IDs for vector DB cleanup.
        
        Args:
            since: Only get tickets deleted after this timestamp
            
        Returns:
            List of deleted ticket_ids
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    if since:
                        cur.execute("""
                            SELECT ticket_id FROM tickets
                            WHERE deleted_at IS NOT NULL
                            AND deleted_at >= %s
                        """, (since,))
                    else:
                        cur.execute("""
                            SELECT ticket_id FROM tickets
                            WHERE deleted_at IS NOT NULL
                        """)
                    
                    rows = cur.fetchall()
                    ticket_ids = [row[0] for row in rows]
                    
                    logger.info(f"📊 Found {len(ticket_ids)} deleted tickets")
                    return ticket_ids
            
        except Exception as e:
            logger.error(f"❌ Error fetching deleted tickets: {e}")
            return []
    
    def get_deleted_messages(self, since: Optional[datetime] = None) -> List[str]:
        """
        Get list of deleted message IDs for vector DB cleanup.
        
        Args:
            since: Only get messages deleted after this timestamp
            
        Returns:
            List of deleted message_ids
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    if since:
                        cur.execute("""
                            SELECT message_id FROM ticket_messages
                            WHERE deleted_at IS NOT NULL
                            AND deleted_at >= %s
                        """, (since,))
                    else:
                        cur.execute("""
                            SELECT message_id FROM ticket_messages
                            WHERE deleted_at IS NOT NULL
                        """)
                    
                    rows = cur.fetchall()
                    message_ids = [row[0] for row in rows]
                    
                    logger.info(f"📊 Found {len(message_ids)} deleted messages")
                    return message_ids
            
        except Exception as e:
            logger.error(f"❌ Error fetching deleted messages: {e}")
            return []
    
    # ===============================
    # CLEANUP DAEMON HELPER METHODS
    # ===============================
    
    def get_soft_delete_candidates(self, cutoff_dt: datetime, limit: int = 100) -> List[str]:
        """
        Get ticket IDs eligible for soft deletion.
        
        Criteria:
        - status = 'Closed'
        - deleted_at IS NULL
        - last_updated < cutoff_dt
        
        Args:
            cutoff_dt: Tickets last updated before this datetime
            limit: Maximum number of tickets to return
            
        Returns:
            List of ticket_id strings
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT ticket_id FROM tickets
                        WHERE status = 'Closed'
                        AND deleted_at IS NULL
                        AND last_updated < %s
                        ORDER BY last_updated ASC
                        LIMIT %s
                    """, (cutoff_dt, limit))
                    
                    rows = cur.fetchall()
                    ticket_ids = [row[0] for row in rows]
                    
                    logger.info(
                        f"🔍 Found {len(ticket_ids)} soft-delete candidates "
                        f"(closed before {cutoff_dt.isoformat()})"
                    )
                    return ticket_ids
            
        except Exception as e:
            logger.error(f"❌ Error fetching soft-delete candidates: {e}")
            return []
    
    def get_hard_delete_candidates(self, cutoff_dt: datetime, limit: int = 100) -> List[str]:
        """
        Get ticket IDs eligible for hard deletion.
        
        Criteria:
        - deleted_at IS NOT NULL
        - deleted_at < cutoff_dt
        
        Args:
            cutoff_dt: Tickets deleted before this datetime
            limit: Maximum number of tickets to return
            
        Returns:
            List of ticket_id strings
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT ticket_id FROM tickets
                        WHERE deleted_at IS NOT NULL
                        AND deleted_at < %s
                        ORDER BY deleted_at ASC
                        LIMIT %s
                    """, (cutoff_dt, limit))
                    
                    rows = cur.fetchall()
                    ticket_ids = [row[0] for row in rows]
                    
                    logger.info(
                        f"🔍 Found {len(ticket_ids)} hard-delete candidates "
                        f"(deleted before {cutoff_dt.isoformat()})"
                    )
                    return ticket_ids
            
        except Exception as e:
            logger.error(f"❌ Error fetching hard-delete candidates: {e}")
            return []
    
    def get_message_ids_for_ticket(self, ticket_id: str) -> List[str]:
        """
        Get all message IDs for a ticket.
        
        Args:
            ticket_id: Ticket ID
            
        Returns:
            List of message_id strings
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT message_id FROM ticket_messages
                        WHERE ticket_id = %s
                    """, (ticket_id,))
                    
                    rows = cur.fetchall()
                    message_ids = [row[0] for row in rows]
                    
                    logger.debug(f"ℹ️  Found {len(message_ids)} messages for ticket {ticket_id}")
                    return message_ids
            
        except Exception as e:
            logger.error(f"❌ Error fetching messages for ticket {ticket_id}: {e}")
            return []
    
    def hard_delete_tickets(self, ticket_ids: List[str]) -> int:
        """
        Permanently delete tickets from database.
        
        Cascades to ticket_messages via foreign key.
        
        Args:
            ticket_ids: List of ticket IDs to delete
            
        Returns:
            int: Number of tickets deleted
        """
        if not ticket_ids:
            return 0
        
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        DELETE FROM tickets
                        WHERE ticket_id = ANY(%s)
                    """, (ticket_ids,))
                    
                    deleted_count = cur.rowcount
                    
                    logger.info(f"🗑️  Hard-deleted {deleted_count} tickets from database")
                    return deleted_count
            
        except Exception as e:
            logger.error(f"❌ Error hard-deleting tickets: {e}")
            return 0
    
    def find_ticket_by_conversation_id(self, conversation_id: str) -> Optional[Dict]:
        """
        Find ticket by conversation_id (excludes soft-deleted tickets).
        
        Args:
            conversation_id: Graph conversation ID
            
        Returns:
            Ticket dict or None
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT * FROM tickets 
                        WHERE conversation_id = %s
                        LIMIT 1
                    """, (conversation_id,))
                    
                    row = cur.fetchone()
                    
                    if row:
                        result = self._convert_ticket_row(dict(row))
                        status = "soft-deleted" if result.get('deleted_at') else "active"
                        logger.debug(f"ℹ️  Found {status} ticket {result['ticket_id']} by conversation_id {conversation_id}")
                        return result
                    
                    return None
            
        except Exception as e:
            logger.error(f"❌ Error finding ticket by conversation_id {conversation_id}: {e}")
            return None
    
    def message_exists(self, message_id: str) -> bool:
        """
        Check if message exists (regardless of soft-delete status).
        
        Args:
            message_id: Message ID to check
            
        Returns:
            bool: True if exists, False otherwise
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT 1 FROM ticket_messages 
                        WHERE message_id = %s
                    """, (message_id,))
                    
                    exists = cur.fetchone() is not None
                    logger.debug(f"ℹ️  Message {message_id[:20]}... exists: {exists}")
                    return exists
            
        except Exception as e:
            logger.error(f"❌ Error checking message existence {message_id[:20]}...: {e}")
            return False
    
    def get_ticket_by_id(self, ticket_id: str) -> Optional[Dict]:
        """
        Get full ticket details by ticket_id (excludes soft-deleted).
        
        Args:
            ticket_id: Ticket ID
            
        Returns:
            Ticket dict or None
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT * FROM tickets 
                        WHERE ticket_id = %s
                        AND deleted_at IS NULL
                    """, (ticket_id,))
                    
                    row = cur.fetchone()
                    
                    if row:
                        return self._convert_ticket_row(dict(row))
                    
                    return None
            
        except Exception as e:
            logger.error(f"❌ Error fetching ticket {ticket_id}: {e}")
            return None
    
    def get_thread_messages(self, ticket_id: str) -> List[Dict]:
        """
        Get all messages for a ticket (ordered chronologically, excludes soft-deleted).
        
        Args:
            ticket_id: Ticket ID
            
        Returns:
            List of message dicts
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT * FROM ticket_messages 
                        WHERE ticket_id = %s
                        AND deleted_at IS NULL
                        ORDER BY timestamp ASC
                    """, (ticket_id,))
                    
                    rows = cur.fetchall()
                    messages = [self._convert_message_row(dict(row)) for row in rows]
                    
                    logger.debug(f"ℹ️  Retrieved {len(messages)} messages for ticket {ticket_id}")
                    return messages
            
        except Exception as e:
            logger.error(f"❌ Error fetching messages for ticket {ticket_id}: {e}")
            return []
    
    def get_active_tickets(self) -> List[Dict]:
        """
        Get all active tickets (excludes soft-deleted).
        
        Returns:
            List of ticket dicts
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT
                            t.*,
                            MAX(m.timestamp) AS last_message_at
                        FROM tickets t
                        LEFT JOIN ticket_messages m
                            ON m.ticket_id = t.ticket_id
                            AND m.deleted_at IS NULL
                        WHERE t.deleted_at IS NULL
                        GROUP BY t.id
                        ORDER BY last_message_at DESC NULLS LAST;

                    """)
                    
                    rows = cur.fetchall()
                    tickets = [self._convert_ticket_row(dict(row)) for row in rows]
                    
                    logger.debug(f"ℹ️  Retrieved {len(tickets)} active tickets")
                    return tickets
                    
        except Exception as e:
            logger.error(f"❌ Error fetching active tickets: {e}")
            return []
    
    def search_tickets(self, query: str) -> List[Dict]:
        """
        Search tickets (excludes soft-deleted).
        
        Args:
            query: Search term
            
        Returns:
            List of matching ticket dicts
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    search_pattern = f"%{query}%"
                    
                    cur.execute("""
                        SELECT * FROM tickets 
                        WHERE deleted_at IS NULL
                        AND (
                            ticket_id ILIKE %s 
                            OR subject ILIKE %s 
                            OR customer_email ILIKE %s
                        )
                        ORDER BY last_updated DESC
                    """, (search_pattern, search_pattern, search_pattern))
                    
                    rows = cur.fetchall()
                    results = [self._convert_ticket_row(dict(row)) for row in rows]
                    
                    logger.info(f"🔍 Search '{query}' returned {len(results)} tickets")
                    return results
                    
        except Exception as e:
            logger.error(f"❌ Error searching tickets with query '{query}': {e}")
            return []
    
    def update_ticket_fields(self, identifier: Union[int, str], fields: dict) -> bool:
        """
        Update specific fields in a ticket by database ID or ticket_id.
        
        Args:
            identifier: Database primary key (int) or ticket_id (str)
            fields: Dict of field names to values
            
        Returns:
            bool: True if successful (at least one row updated), False otherwise
        """
        try:
            if not fields:
                return False
            
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    # Build UPDATE statement dynamically
                    set_parts = []
                    values = []
                    
                    for key, value in fields.items():
                        set_parts.append(sql.Identifier(key))
                        values.append(value)
                    
                    # Determine which column to filter by based on identifier type
                    if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
                        where_col = "id"
                    else:
                        where_col = "ticket_id"
                        
                    query = sql.SQL("UPDATE tickets SET {} WHERE {} = %s").format(
                        sql.SQL(", ").join(
                            sql.SQL("{} = %s").format(ident) 
                            for ident in set_parts
                        ),
                        sql.Identifier(where_col)
                    )
                    
                    values.append(identifier)
                    cur.execute(query, values)
                    
                    if cur.rowcount > 0:
                        logger.info(f"✅ Updated ticket {identifier} | Fields: {list(fields.keys())}")
                        return True
                    else:
                        logger.warning(f"⚠️  No ticket found with identifier {identifier}")
                        return False
            
        except Exception as e:
            logger.error(f"❌ Error updating ticket row {row_number}: {e}")
            return False
    
    # Helper methods
    
    def _convert_ticket_row(self, row: Dict) -> Dict:
        """Convert PostgreSQL ticket row to expected format"""
        if not row:
            return row

        if isinstance(row.get('created_at'), datetime):
            row['created_at'] = row['created_at'].isoformat()

        if isinstance(row.get('last_updated'), datetime):
            row['last_updated'] = row['last_updated'].isoformat()

        if isinstance(row.get('last_message_at'), datetime):  
            row['last_message_at'] = row['last_message_at'].isoformat()

        if isinstance(row.get('deleted_at'), datetime):
            row['deleted_at'] = row['deleted_at'].isoformat()

        # Parse RAG provenance if present
        if row.get('rag_provenance') and isinstance(row['rag_provenance'], str):
            try:
                row['rag_provenance'] = json.loads(row['rag_provenance'])
            except json.JSONDecodeError:
                row['rag_provenance'] = None

        return row

    
    def _convert_message_row(self, row: Dict) -> Dict:
        """Convert PostgreSQL message row to expected format"""
        if not row:
            return row
        
        if isinstance(row.get('timestamp'), datetime):
            row['timestamp'] = row['timestamp'].isoformat()
        
        if isinstance(row.get('deleted_at'), datetime):
            row['deleted_at'] = row['deleted_at'].isoformat()
        
        if 'is_internal' in row:
            row['is_internal'] = 1 if row['is_internal'] else 0
        
        if isinstance(row.get('attachments'), str):
            try:
                row['attachments'] = json.loads(row['attachments'])
            except json.JSONDecodeError:
                row['attachments'] = []
        
        return row
    
    def close(self):
        """Close all database connections"""
        self.conn_manager.close_all()