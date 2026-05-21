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
import time
import hashlib

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] %(message)s'
)
logger = logging.getLogger(__name__)


class ActionType:
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


class ActorType:
    SYSTEM = "SYSTEM"
    USER = "USER"
    AI = "AI"


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
                            display_id TEXT UNIQUE,
                            conversation_id TEXT,
                            subject TEXT,
                            customer_email TEXT,
                            status TEXT DEFAULT 'Open',
                            assigned_to TEXT DEFAULT 'Unassigned',
                            ai_draft TEXT,
                            rag_provenance JSONB,
                            issue_state JSONB,
                            is_authority BOOLEAN DEFAULT FALSE,
                            created_at TIMESTAMPTZ NOT NULL,
                            last_updated TIMESTAMPTZ NOT NULL,
                            deleted_at TIMESTAMPTZ,
                            reopened BOOLEAN DEFAULT FALSE,
                            needs_ai_generation BOOLEAN DEFAULT FALSE,
                            has_unread_response BOOLEAN DEFAULT FALSE
                        )
                    """)
                    
                    # Ensure new columns exist (for existing databases)
                    cur.execute("""
                        DO $$ 
                        BEGIN 
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                           WHERE table_name='tickets' AND column_name='reopened') THEN 
                                ALTER TABLE tickets ADD COLUMN reopened BOOLEAN DEFAULT FALSE;
                            END IF;
                            
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                           WHERE table_name='tickets' AND column_name='display_id') THEN 
                                ALTER TABLE tickets ADD COLUMN display_id TEXT UNIQUE;
                            END IF;

                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                           WHERE table_name='tickets' AND column_name='needs_ai_generation') THEN 
                                ALTER TABLE tickets ADD COLUMN needs_ai_generation BOOLEAN DEFAULT FALSE;
                            END IF;

                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                           WHERE table_name='tickets' AND column_name='source_received_at') THEN 
                                ALTER TABLE tickets ADD COLUMN source_received_at TIMESTAMPTZ;
                            END IF;

                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                           WHERE table_name='tickets' AND column_name='issue_state') THEN 
                                ALTER TABLE tickets ADD COLUMN issue_state JSONB;
                            END IF;

                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                           WHERE table_name='tickets' AND column_name='is_authority') THEN 
                                ALTER TABLE tickets ADD COLUMN is_authority BOOLEAN DEFAULT FALSE;
                            END IF;

                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                           WHERE table_name='tickets' AND column_name='has_unread_response') THEN 
                                ALTER TABLE tickets ADD COLUMN has_unread_response BOOLEAN DEFAULT FALSE;
                            END IF;
                        END $$;
                    """)

                    try:
                        # Trigger backfill for any tickets without display_id
                        self.backfill_display_ids()
                    except Exception as backfill_err:
                        logger.warning(f"⚠️ Backfill failed but continuing: {backfill_err}")
                    
                    # 2. CHILD TABLE: MESSAGES
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS ticket_messages (
                            id SERIAL PRIMARY KEY,
                            ticket_id TEXT NOT NULL,
                            message_id TEXT UNIQUE NOT NULL,
                            sender TEXT,
                            to_email TEXT DEFAULT '',
                            body_text TEXT,
                            timestamp TIMESTAMPTZ,
                            attachments TEXT,
                            cc TEXT DEFAULT '',
                            bcc TEXT DEFAULT '',
                            is_internal BOOLEAN DEFAULT FALSE,
                            deleted_at TIMESTAMPTZ,
                            FOREIGN KEY(ticket_id) REFERENCES tickets(ticket_id) ON DELETE CASCADE
                        )
                    """)
                    
                    cur.execute("""
                        DO $$ 
                        BEGIN 
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                           WHERE table_name='ticket_messages' AND column_name='cc') THEN 
                                ALTER TABLE ticket_messages ADD COLUMN cc TEXT DEFAULT '';
                            END IF;
                            
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                           WHERE table_name='ticket_messages' AND column_name='bcc') THEN 
                                ALTER TABLE ticket_messages ADD COLUMN bcc TEXT DEFAULT '';
                            END IF;

                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                           WHERE table_name='ticket_messages' AND column_name='body_html') THEN 
                                ALTER TABLE ticket_messages ADD COLUMN body_html TEXT;
                            END IF;

                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                           WHERE table_name='ticket_messages' AND column_name='internet_message_id') THEN 
                                ALTER TABLE ticket_messages ADD COLUMN internet_message_id TEXT;
                            END IF;

                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                           WHERE table_name='ticket_messages' AND column_name='to_email') THEN 
                                ALTER TABLE ticket_messages ADD COLUMN to_email TEXT DEFAULT '';
                            END IF;
                        END $$;
                    """)
                    
                    # 3. ATTACHMENT CONTEXT TABLE (NEW)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS ticket_attachments_context (
                            id SERIAL PRIMARY KEY,
                            ticket_id TEXT NOT NULL,
                            message_id TEXT NOT NULL,
                            filename TEXT,
                            content_summary TEXT,
                            metadata JSONB,
                            created_at TIMESTAMPTZ DEFAULT NOW(),
                            FOREIGN KEY(ticket_id) REFERENCES tickets(ticket_id) ON DELETE CASCADE
                        )
                    """)

                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_attach_ctx_ticket 
                        ON ticket_attachments_context(ticket_id)
                    """)

                    # 4. INDEXES
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

                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_msg_internet_id
                        ON ticket_messages(internet_message_id) 
                        WHERE internet_message_id IS NOT NULL
                    """)
                    
                    # 5. SLA TRACKING TABLE: TICKET_EVENTS
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS ticket_events (
                            id SERIAL PRIMARY KEY,
                            ticket_id TEXT NOT NULL,
                            event_type TEXT NOT NULL,
                            actor TEXT,
                            timestamp TIMESTAMPTZ DEFAULT NOW(),
                            details JSONB,
                            FOREIGN KEY(ticket_id) REFERENCES tickets(ticket_id) ON DELETE CASCADE
                        )
                    """)
                    
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_event_ticket 
                        ON ticket_events(ticket_id)
                    """)
                    
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_event_timestamp 
                        ON ticket_events(timestamp DESC)
                    """)

                    # 6. NEW AUDIT LOGS TABLE (v2)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS ticket_audit_logs (
                            id SERIAL PRIMARY KEY,
                            ticket_id TEXT NOT NULL,
                            action_type TEXT NOT NULL,
                            actor_type TEXT NOT NULL,
                            actor_id TEXT NOT NULL,
                            description TEXT,
                            metadata JSONB,
                            timestamp TIMESTAMPTZ DEFAULT NOW(),
                            event_hash TEXT UNIQUE,
                            FOREIGN KEY(ticket_id) REFERENCES tickets(ticket_id) ON DELETE CASCADE
                        )
                    """)

                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_audit_ticket 
                        ON ticket_audit_logs(ticket_id)
                    """)

                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_audit_timestamp 
                        ON ticket_audit_logs(timestamp DESC)
                    """)

                    # 7. TICKET METRICS TABLE (Flattened for Analytics)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS ticket_metrics (
                            ticket_id TEXT PRIMARY KEY,
                            customer_email TEXT,
                            assigned_agent TEXT,
                            created_at TIMESTAMPTZ,
                            first_response_at TIMESTAMPTZ,
                            resolved_at TIMESTAMPTZ,
                            first_response_duration INTEGER,
                            total_resolution_duration INTEGER,
                            total_customer_wait_duration INTEGER,
                            total_agent_work_duration INTEGER,
                            message_count_customer INTEGER DEFAULT 0,
                            message_count_agent INTEGER DEFAULT 0,
                            reopen_count INTEGER DEFAULT 0,
                            sla_frt_breached BOOLEAN DEFAULT FALSE,
                            sla_resolution_breached BOOLEAN DEFAULT FALSE,
                            last_updated_at TIMESTAMPTZ DEFAULT NOW(),
                            FOREIGN KEY(ticket_id) REFERENCES tickets(ticket_id) ON DELETE CASCADE
                        )
                    """)

                    cur.execute("CREATE INDEX IF NOT EXISTS idx_metrics_agent ON ticket_metrics(assigned_agent)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_metrics_created ON ticket_metrics(created_at)")
                    
            logger.info("✅ PostgreSQL schema initialized (with provenance + soft-delete support)")
            return True
            
        except Exception as e:
            logger.error(f"❌ Schema initialization failed: {e}")
            return False
    
    def create_ticket(self, ticket_id: str, conversation_id: str, subject: str, 
                     customer_email: str, actor: str = 'system', source_received_at: Union[datetime, str] = None) -> bool:
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
                    now = datetime.now() # System creation time is ALWAYS local now
                    
                    # Ensure source_received_at is parsed if provided
                    parsed_source_time = source_received_at
                    if isinstance(parsed_source_time, str):
                        try:
                            if parsed_source_time.endswith('Z'):
                                parsed_source_time = parsed_source_time.replace('Z', '+00:00')
                            parsed_source_time = datetime.fromisoformat(parsed_source_time)
                        except:
                            parsed_source_time = None

                    # Generate human-readable Display ID based on SYSTEM creation time
                    domain = customer_email.split('@')[-1].split('.')[0].upper()
                    date_str = now.strftime('%m%d')
                    
                    # Count existing tickets for this domain today to get sequence
                    cur.execute("""
                        SELECT COUNT(*) FROM tickets 
                        WHERE customer_email LIKE %s 
                        AND created_at >= %s
                    """, (f'%@{customer_email.split("@")[-1]}', now.replace(hour=0, minute=0, second=0, microsecond=0)))
                    seq = cur.fetchone()[0] + 1
                    display_id = f"{domain}-{date_str}-{seq:02d}"

                    # New tickets start as Unassigned
                    assignee = 'Unassigned'
                    
                    cur.execute("""
                        INSERT INTO tickets (
                            ticket_id, display_id, conversation_id, subject, 
                            customer_email, created_at, last_updated,
                            assigned_to, source_received_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (ticket_id) DO NOTHING
                    """, (ticket_id, display_id, conversation_id, subject, customer_email, now, now, assignee, parsed_source_time))
                    
                    created = cur.rowcount > 0
                    
                    if created:
                        logger.info(f"✅ Created ticket {display_id} | Customer: {customer_email} | Subject: {subject[:50]}")
                        # Log Audit Event using SOURCE time for timeline accuracy
                        self.log_audit_event(
                            ticket_id=ticket_id,
                            action_type=ActionType.TICKET_CREATED,
                            actor_type=ActorType.USER if actor != 'system' else ActorType.SYSTEM,
                            actor_id=actor,
                            description=f"Ticket created in system.",
                            cur=cur,
                            timestamp=parsed_source_time or now
                        )
                        # No assignment event here as it starts unassigned
                    else:
                        logger.debug(f"ℹ️  Ticket {ticket_id} already exists (idempotent)")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error creating ticket {ticket_id}: {e}")
            return False

    def backfill_display_ids(self):
        """Backfill display_id for existing tickets that don't have one."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id, customer_email, created_at FROM tickets WHERE display_id IS NULL ORDER BY created_at ASC")
                    rows = cur.fetchall()
                    
                    for row_id, email, created in rows:
                        domain = email.split('@')[-1].split('.')[0].upper()
                        date_str = created.strftime('%m%d')
                        
                        # Use ID as sequence for backfill to ensure uniqueness
                        display_id = f"{domain}-{date_str}-X{row_id}"
                        cur.execute("UPDATE tickets SET display_id = %s WHERE id = %s", (display_id, row_id))
                    
                    logger.info(f"✅ Backfilled display_id for {len(rows)} tickets")
        except Exception as e:
            logger.error(f"❌ Error backfilling display_ids: {e}")
    
    def _log_ticket_event_with_cursor(self, cur, ticket_id: str, event_type: str, actor: str, details: dict = None, timestamp: datetime = None):
        """Internal helper to log event using an existing cursor/transaction."""
        cur.execute("""
            INSERT INTO ticket_events (ticket_id, event_type, actor, details, timestamp)
            VALUES (%s, %s, %s, %s, %s)
        """, (ticket_id, event_type, actor, json.dumps(details) if details else None, timestamp or datetime.now()))

    def log_audit_event(self, ticket_id: str, action_type: str, actor_id: str, 
                        actor_type: str = ActorType.SYSTEM, description: str = None, 
                        metadata: dict = None, cur = None, timestamp: datetime = None) -> bool:
        """
        Standardized logging for all ticket events with idempotency.
        """
        try:
            # Ensure timestamp is a datetime object if a string was passed
            if isinstance(timestamp, str):
                try:
                    if timestamp.endswith('Z'):
                        timestamp = timestamp.replace('Z', '+00:00')
                    timestamp = datetime.fromisoformat(timestamp)
                except:
                    timestamp = None # Fallback to NOW() later

            # 1. Generate idempotency hash (action + ticket + actor + 5sec window)
            time_window = int(time.time() / 5)
            hash_input = f"{ticket_id}:{action_type}:{actor_id}:{time_window}"
            event_hash = hashlib.md5(hash_input.encode()).hexdigest()

            query = """
                INSERT INTO ticket_audit_logs (
                    ticket_id, action_type, actor_type, actor_id, 
                    description, metadata, event_hash, timestamp
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_hash) DO NOTHING
            """
            params = (
                ticket_id, action_type, actor_type, actor_id,
                description, json.dumps(metadata) if metadata else None,
                event_hash,
                timestamp or datetime.now()
            )

            if cur:
                # Use provided cursor
                cur.execute(query, params)
                if cur.rowcount > 0:
                    legacy_type_map = {
                        ActionType.TICKET_CREATED: 'created',
                        ActionType.MESSAGE_RECEIVED: 'email_received',
                        ActionType.MESSAGE_SENT: 'responded',
                        ActionType.NOTE_ADDED: 'note_added',
                        ActionType.STATUS_CHANGED: 'status_changed',
                        ActionType.ASSIGNMENT_CHANGED: 'assigned',
                        ActionType.AI_DRAFT_GENERATED: 'draft_saved',
                        ActionType.TICKET_DELETED: 'closed'
                    }
                    legacy_type = legacy_type_map.get(action_type, action_type.lower())
                    self._log_ticket_event_with_cursor(cur, ticket_id, legacy_type, actor_id, metadata, timestamp=timestamp)
                    self._update_ticket_metrics_with_cursor(cur, ticket_id, action_type, actor_type, actor_id, metadata, timestamp=timestamp)
            else:
                # Open new connection
                with self.conn_manager.get_connection() as conn:
                    with conn.cursor() as cur_new:
                        cur_new.execute(query, params)
                        if cur_new.rowcount > 0:
                            legacy_type_map = {
                                ActionType.TICKET_CREATED: 'created',
                                ActionType.MESSAGE_RECEIVED: 'email_received',
                                ActionType.MESSAGE_SENT: 'responded',
                                ActionType.NOTE_ADDED: 'note_added',
                                ActionType.STATUS_CHANGED: 'status_changed',
                                ActionType.ASSIGNMENT_CHANGED: 'assigned',
                                ActionType.AI_DRAFT_GENERATED: 'draft_saved',
                                ActionType.TICKET_DELETED: 'closed'
                            }
                            legacy_type = legacy_type_map.get(action_type, action_type.lower())
                            self._log_ticket_event_with_cursor(cur_new, ticket_id, legacy_type, actor_id, metadata, timestamp=timestamp)
                            self._update_ticket_metrics_with_cursor(cur_new, ticket_id, action_type, actor_type, actor_id, metadata, timestamp=timestamp)
                        
            return True
        except Exception as e:
            logger.error(f"❌ Error logging audit event {action_type} for {ticket_id}: {e}")
            return False

    def _update_ticket_metrics_with_cursor(self, cur, ticket_id: str, action_type: str, 
                                         actor_type: str, actor_id: str, metadata: dict, timestamp: datetime = None):
        """
        Incrementally update the ticket_metrics table based on a new audit event.
        """
        try:
            event_time = timestamp or datetime.now()

            # Ensure a metrics row exists
            cur.execute("""
                INSERT INTO ticket_metrics (ticket_id, created_at, customer_email, assigned_agent)
                SELECT ticket_id, created_at, customer_email, assigned_to
                FROM tickets WHERE ticket_id = %s
                ON CONFLICT (ticket_id) DO UPDATE SET 
                    customer_email = EXCLUDED.customer_email,
                    assigned_agent = EXCLUDED.assigned_agent,
                    last_updated_at = %s
            """, (ticket_id, event_time))

            if action_type == ActionType.TICKET_CREATED:
                cur.execute("UPDATE ticket_metrics SET created_at = %s WHERE ticket_id = %s", (event_time, ticket_id))

            elif action_type == ActionType.MESSAGE_RECEIVED:
                cur.execute("""
                    UPDATE ticket_metrics 
                    SET message_count_customer = message_count_customer + 1,
                        last_updated_at = %s
                    WHERE ticket_id = %s
                """, (event_time, ticket_id))

            elif action_type == ActionType.MESSAGE_SENT:
                # Calculate FRT if not already set
                cur.execute("""
                    UPDATE ticket_metrics 
                    SET first_response_at = COALESCE(first_response_at, %s),
                        first_response_duration = COALESCE(first_response_duration, 
                            EXTRACT(EPOCH FROM (%s - created_at))::INT),
                        message_count_agent = message_count_agent + 1,
                        last_updated_at = %s
                    WHERE ticket_id = %s
                """, (event_time, event_time, event_time, ticket_id))

            elif action_type == ActionType.NOTE_ADDED:
                # OPTIONAL: Treat internal note as a response if configured
                if Config.INTERNAL_NOTE_AS_RESPONSE:
                    cur.execute("""
                        UPDATE ticket_metrics 
                        SET first_response_at = COALESCE(first_response_at, %s),
                            first_response_duration = COALESCE(first_response_duration, 
                                EXTRACT(EPOCH FROM (%s - created_at))::INT),
                            message_count_agent = message_count_agent + 1,
                            last_updated_at = %s
                        WHERE ticket_id = %s
                    """, (event_time, event_time, event_time, ticket_id))

            elif action_type == ActionType.STATUS_CHANGED:
                new_status = (metadata or {}).get('to', '').lower()
                old_status = (metadata or {}).get('from', '').lower()
                
                if new_status in ['closed', 'resolved']:
                    cur.execute("""
                        UPDATE ticket_metrics 
                        SET resolved_at = %s,
                            total_resolution_duration = EXTRACT(EPOCH FROM (%s - created_at))::INT,
                            last_updated_at = %s
                        WHERE ticket_id = %s
                    """, (event_time, event_time, event_time, ticket_id))
                
                if old_status in ['closed', 'resolved'] and new_status not in ['closed', 'resolved']:
                    cur.execute("""
                        UPDATE ticket_metrics 
                        SET reopen_count = reopen_count + 1,
                            resolved_at = NULL,
                            last_updated_at = %s
                        WHERE ticket_id = %s
                    """, (event_time, ticket_id))

            elif action_type == ActionType.ASSIGNMENT_CHANGED:
                new_agent = (metadata or {}).get('to')
                cur.execute("UPDATE ticket_metrics SET assigned_agent = %s WHERE ticket_id = %s", (new_agent, ticket_id))

        except Exception as e:
            logger.error(f"⚠️ Failed to update metrics for {ticket_id}: {e}")

    def log_ticket_event(self, ticket_id: str, event_type: str, actor: str, details: dict = None, cur = None) -> bool:
        """
        Legacy logging method - redirected to new audit system.
        """
        # Map legacy types to new ActionTypes
        mapping = {
            'created': ActionType.TICKET_CREATED,
            'email_received': ActionType.MESSAGE_RECEIVED,
            'responded': ActionType.MESSAGE_SENT,
            'note_added': ActionType.NOTE_ADDED,
            'assigned': ActionType.ASSIGNMENT_CHANGED,
            'ticket_taken': ActionType.ASSIGNMENT_CHANGED,
            'ticket_assigned': ActionType.ASSIGNMENT_CHANGED,
            'closed': ActionType.STATUS_CHANGED,
            'reopened': ActionType.TICKET_RESTORED,
            'draft_saved': ActionType.AI_DRAFT_UPDATED
        }
        
        action = mapping.get(event_type, event_type.upper())
        actor_type = ActorType.SYSTEM if actor == 'system' else ActorType.USER
        
        return self.log_audit_event(
            ticket_id=ticket_id,
            action_type=action,
            actor_type=actor_type,
            actor_id=actor,
            description=f"Action: {event_type} performed by {actor}",
            metadata=details,
            cur=cur
        )
    
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
                    # Deduplication Strategy: Reconcile placeholder versions of instantly logged sent replies
                    if message_id and not message_id.startswith('sent_'):
                        body_content = email.get('body_html') or email.get('body_text') or email.get('body') or ''
                        cur.execute("""
                            SELECT message_id FROM ticket_messages 
                            WHERE ticket_id = %s 
                            AND message_id LIKE 'sent_%%'
                            AND (body_html = %s OR body_text = %s)
                            LIMIT 1
                        """, (ticket_id, body_content, body_content))
                        placeholder = cur.fetchone()
                        if placeholder:
                            placeholder_id = placeholder[0]
                            logger.info(f"🔄 Reconciling placeholder {placeholder_id} with real Graph ID {message_id}")
                            cur.execute("""
                                UPDATE ticket_messages 
                                SET message_id = %s, internet_message_id = %s, timestamp = %s
                                WHERE message_id = %s
                            """, (message_id, email.get('internet_message_id'), email.get('received'), placeholder_id))
                            return True

                    cur.execute("""
                        INSERT INTO ticket_messages (
                            ticket_id, message_id, internet_message_id, sender, to_email, body_text,
                            body_html, timestamp, attachments, cc, bcc, is_internal
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (message_id) DO NOTHING
                    """, (
                        ticket_id,
                        message_id,
                        email.get('internet_message_id'),
                        email.get('sender'),
                        email.get('to') or email.get('to_email') or '',
                        email.get('body_text') or email.get('body'),
                        email.get('body_html') or email.get('body'),
                        email.get('received'),
                        json.dumps(email.get('attachments', [])),
                        email.get('cc', ''),
                        email.get('bcc', ''),
                        is_internal
                    ))
                    
                    logged = cur.rowcount > 0
                    
                    received_time = email.get('received')
                    
                    # Update ticket's last_updated timestamp (System metadata uses NOW)
                    if logged:
                        cur.execute("""
                            UPDATE tickets 
                            SET last_updated = %s,
                                has_unread_response = %s
                            WHERE ticket_id = %s
                        """, (datetime.now(), not is_internal, ticket_id))
                    else:
                        cur.execute("""
                            UPDATE tickets 
                            SET last_updated = %s 
                            WHERE ticket_id = %s
                        """, (datetime.now(), ticket_id))
                    
                    if logged:
                        speaker = "INTERNAL" if is_internal else "CUSTOMER"
                        # Resolve friendly ID for logging
                        display_id = self.get_display_id(ticket_id)
                        logger.info(
                            f"✅ Logged message {message_id[:20]}... to ticket {display_id} "
                            f"| Speaker: {speaker} | Sender: {email.get('sender', 'unknown')}"
                        )
                        # Log Audit Event
                        if not is_internal:
                            self.log_audit_event(
                                ticket_id=ticket_id,
                                action_type=ActionType.MESSAGE_RECEIVED,
                                actor_type=ActorType.SYSTEM,
                                actor_id=email.get('sender', 'unknown'),
                                description=f"New message received from customer.",
                                cur=cur,
                                timestamp=received_time
                            )
                        else:
                            self.log_audit_event(
                                ticket_id=ticket_id,
                                action_type=ActionType.NOTE_ADDED,
                                actor_type=ActorType.USER,
                                actor_id=email.get('sender', 'unknown'),
                                description=f"Internal note added by staff.",
                                cur=cur,
                                timestamp=received_time
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
                                last_updated = %s,
                                needs_ai_generation = FALSE
                            WHERE ticket_id = %s
                        """, (draft, provenance_json, datetime.now(), ticket_id))
                        
                        # Resolve friendly ID for logging
                        display_id = self.get_display_id(ticket_id)
                        logger.info(
                            f"✅ Updated AI draft for ticket {display_id} "
                            f"| Provenance: {len(provenance)} RAG docs stored"
                        )
                        self.log_audit_event(ticket_id, ActionType.AI_DRAFT_GENERATED, "AI_AGENT", ActorType.AI, "AI generated a response draft.")
                    else:
                        cur.execute("""
                            UPDATE tickets 
                            SET ai_draft = %s, 
                                last_updated = %s,
                                needs_ai_generation = FALSE
                            WHERE ticket_id = %s
                        """, (draft, datetime.now(), ticket_id))
                        
                        # Resolve friendly ID for logging
                        display_id = self.get_display_id(ticket_id)
                        logger.info(f"✅ Updated AI draft for ticket {display_id} (no provenance)")
                        self.log_audit_event(ticket_id, ActionType.AI_DRAFT_GENERATED, "AI_AGENT", ActorType.AI, "AI generated a response draft (no context).")
            
            return True
            
        except Exception as e:
            # Fallback to ticket_id for error log if resolution fails
            logger.error(f"❌ Error updating AI draft for ticket {ticket_id}: {e}")
            return False
    
    def flag_for_ai_regeneration(self, ticket_id: str) -> bool:
        """
        Flag a ticket to have its AI response regenerated by the main daemon.
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE tickets 
                        SET needs_ai_generation = TRUE,
                            ai_draft = 'Regenerating...',
                            last_updated = %s
                        WHERE ticket_id = %s
                    """, (datetime.now(), ticket_id))
                    
                    if cur.rowcount > 0:
                        display_id = self.get_display_id(ticket_id)
                        logger.info(f"✅ Flagged ticket {display_id} for AI regeneration")
                        return True
                    return False
        except Exception as e:
            logger.error(f"❌ Error flagging ticket {ticket_id} for regeneration: {e}")
            return False

    def mark_ticket_as_read(self, ticket_id: str) -> bool:
        """
        Mark a ticket's unread response flag as read (FALSE).
        
        Returns True if the flag was previously TRUE and is now set to FALSE.
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    # Only return True if we actually changed the value from TRUE to FALSE
                    cur.execute("""
                        UPDATE tickets 
                        SET has_unread_response = FALSE 
                        WHERE ticket_id = %s AND has_unread_response = TRUE
                    """, (ticket_id,))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error(f"❌ Error marking ticket {ticket_id} as read: {e}")
            return False

    def get_tickets_needing_regeneration(self) -> List[str]:
        """
        Get all ticket IDs that need AI regeneration.
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT ticket_id 
                        FROM tickets 
                        WHERE needs_ai_generation = TRUE AND deleted_at IS NULL
                    """)
                    
                    rows = cur.fetchall()
                    return [row[0] for row in rows]
        except Exception as e:
            logger.error(f"❌ Error getting tickets needing regeneration: {e}")
            return []
    
    def update_ticket_issue_state(self, ticket_id: str, issue_state: Dict) -> bool:
        """Update the persistent issue state for a ticket."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE tickets 
                        SET issue_state = %s::jsonb,
                            last_updated = %s
                        WHERE ticket_id = %s
                    """, (json.dumps(issue_state), datetime.now(), ticket_id))
                    
                    if cur.rowcount > 0:
                        display_id = self.get_display_id(ticket_id)
                        logger.info(f"✅ Updated issue state for ticket {display_id}")
                        return True
                    return False
        except Exception as e:
            logger.error(f"❌ Error updating issue state for ticket {ticket_id}: {e}")
            return False

    def get_display_id(self, ticket_id: str) -> str:
        """Resolve a long Outlook ticket_id to its friendly display_id."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT display_id FROM tickets WHERE ticket_id = %s", (ticket_id,))
                    res = cur.fetchone()
                    return res[0] if res else ticket_id
        except Exception as e:
            logger.error(f"Error resolving display_id for {ticket_id}: {e}")
            return ticket_id

    def get_ticket_issue_state(self, ticket_id: str) -> Optional[Dict]:
        """Retrieve the persistent issue state for a ticket."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT issue_state FROM tickets WHERE ticket_id = %s", (ticket_id,))
                    row = cur.fetchone()
                    if row and row[0]:
                        return row[0]
                    return None
        except Exception as e:
            logger.error(f"❌ Error fetching issue state for ticket {ticket_id}: {e}")
            return None

    def store_attachment_context(self, ticket_id: str, message_id: str, filename: str, 
                                 content_summary: str, metadata: Dict = None) -> bool:
        """Store processed attachment context linked to a ticket."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO ticket_attachments_context (
                            ticket_id, message_id, filename, content_summary, metadata
                        ) VALUES (%s, %s, %s, %s, %s)
                    """, (ticket_id, message_id, filename, content_summary, json.dumps(metadata) if metadata else None))
                    return True
        except Exception as e:
            logger.error(f"❌ Error storing attachment context for ticket {ticket_id}: {e}")
            return False

    def get_ticket_attachments_context(self, ticket_id: str) -> List[Dict]:
        """Retrieve all attachment contexts for a ticket."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT * FROM ticket_attachments_context 
                        WHERE ticket_id = %s 
                        ORDER BY created_at ASC
                    """, (ticket_id,))
                    return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"❌ Error fetching attachment context for ticket {ticket_id}: {e}")
            return []

    def mark_ticket_as_authority(self, ticket_id: str, is_authority: bool = True) -> bool:
        """Mark a ticket as an authoritative reference for RAG (Gold Label)."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE tickets 
                        SET is_authority = %s,
                            last_updated = %s
                        WHERE ticket_id = %s
                    """, (is_authority, datetime.now(), ticket_id))
                    
                    if cur.rowcount > 0:
                        display_id = self.get_display_id(ticket_id)
                        logger.info(f"🏆 Ticket {display_id} marked as authority: {is_authority}")
                        return True
                    return False
        except Exception as e:
            logger.error(f"❌ Error marking ticket {ticket_id} as authority: {e}")
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
                        display_id = self.get_display_id(ticket_id)
                        logger.info(f"✅ Soft-deleted ticket {display_id}")
                        self.log_audit_event(ticket_id, ActionType.TICKET_DELETED, "system", ActorType.SYSTEM, "Ticket soft-deleted.")
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
                        display_id = self.get_display_id(ticket_id)
                        logger.info(f"🔄 Re-opened ticket {display_id}")
                        self.log_audit_event(ticket_id, ActionType.TICKET_RESTORED, "system", ActorType.SYSTEM, "Ticket re-opened.")
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
    
    def message_exists(self, message_id: str, internet_message_id: Optional[str] = None) -> bool:
        """
        Check if message exists in the database.
        Checks both Graph ID and InternetMessageId for robust deduplication across folders.
        
        Args:
            message_id: Graph message ID
            internet_message_id: Global Internet Message ID
            
        Returns:
            bool: True if exists, False otherwise
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    if internet_message_id:
                        cur.execute("""
                            SELECT 1 FROM ticket_messages 
                            WHERE message_id = %s OR internet_message_id = %s
                        """, (message_id, internet_message_id))
                    else:
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
                        SELECT t.*, 
                               (SELECT MAX(timestamp) FROM ticket_messages WHERE ticket_id = t.ticket_id) as last_message_at
                        FROM tickets t 
                        WHERE t.ticket_id = %s
                        AND t.deleted_at IS NULL
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
            logger.error(f"❌ Error retrieving messages: {e}")
            return []

    def get_least_busy_staff(self) -> str:
        """
        Find the active staff member with the fewest open tickets.
        
        Returns:
            Username of least busy staff, or 'Unassigned' if none available
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    # Query for least busy active staff
                    # Statuses that count as 'Open': Open, Pending Review, Pending, In Progress
                    query = """
                        SELECT u.username
                        FROM users u
                        LEFT JOIN tickets t ON u.username = t.assigned_to 
                            AND t.deleted_at IS NULL 
                            AND t.status IN ('Open', 'Pending Review', 'Pending', 'In Progress')
                        WHERE u.is_active = True AND u.is_assignable = True
                        GROUP BY u.username
                        ORDER BY COUNT(t.ticket_id) ASC
                        LIMIT 1
                    """
                    cur.execute(query)
                    result = cur.fetchone()
                    
                    if result:
                        return result[0]
                    return 'Unassigned'
        except Exception as e:
            logger.error(f"❌ Error finding least busy staff: {e}")
            return 'Unassigned'

    def get_active_tickets(self, assigned_to: Optional[str] = None) -> List[Dict]:
        """
        Get all active tickets (excludes soft-deleted).
        
        Args:
            assigned_to: Optional filter by assignee username
            
        Returns:
            List of ticket dicts
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    query = """
                        SELECT t.*, 
                               (SELECT MAX(timestamp) FROM ticket_messages WHERE ticket_id = t.ticket_id) as last_message_at
                        FROM tickets t
                        WHERE t.deleted_at IS NULL
                    """
                    params = []
                    
                    if assigned_to:
                        query += " AND assigned_to = %s"
                        params.append(assigned_to)
                        
                    query += """
                        ORDER BY last_updated DESC NULLS LAST;
                    """
                    
                    cur.execute(query, params)
                    
                    rows = cur.fetchall()
                    tickets = [self._convert_ticket_row(dict(row)) for row in rows]
                    
                    logger.debug(f"ℹ️  Retrieved {len(tickets)} active tickets (Filter: {assigned_to})")
                    return tickets
                    
        except Exception as e:
            logger.error(f"❌ Error fetching active tickets: {e}")
            return []
 
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
                        SELECT t.*, 
                               (SELECT MAX(timestamp) FROM ticket_messages WHERE ticket_id = t.ticket_id) as last_message_at
                        FROM tickets t 
                        WHERE t.deleted_at IS NULL
                        AND (
                            t.ticket_id ILIKE %s 
                            OR t.display_id ILIKE %s
                            OR t.subject ILIKE %s 
                            OR t.customer_email ILIKE %s
                        )
                        ORDER BY t.last_updated DESC
                    """, (search_pattern, search_pattern, search_pattern, search_pattern))
                    
                    rows = cur.fetchall()
                    results = [self._convert_ticket_row(dict(row)) for row in rows]
                    
                    logger.info(f"🔍 Search '{query}' returned {len(results)} tickets")
                    return results
                    
        except Exception as e:
            logger.error(f"❌ Error searching tickets with query '{query}': {e}")
            return []
    
    def update_ticket_fields(self, identifier: Union[int, str], fields: dict, actor: str = 'system') -> bool:
        """
        Update specific fields in a ticket by database ID or ticket_id.
        """
        try:
            if not fields:
                return False
            
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    set_parts = []
                    values = []
                    
                    for key, value in fields.items():
                        set_parts.append(sql.Identifier(key))
                        values.append(value)
                    
                    if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
                        where_col = "id"
                    else:
                        where_col = "ticket_id"
                        
                    # Pre-fetch existing state for activity logging
                    old_assignment = None
                    old_status = None
                    t_id = identifier if where_col == "ticket_id" else None
                    
                    if 'assigned_to' in fields or 'status' in fields:
                        cur.execute(f"SELECT ticket_id, assigned_to, status FROM tickets WHERE {where_col} = %s", (identifier,))
                        row = cur.fetchone()
                        if row:
                            if not t_id: t_id = row[0]
                            old_assignment = row[1]
                            old_status = row[2]
                        
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
                        # Fetch display_id for nicer logging
                        cur.execute(f"SELECT display_id FROM tickets WHERE {where_col} = %s", (identifier,))
                        d_row = cur.fetchone()
                        d_id = d_row[0] if d_row else identifier
                        
                        logger.info(f"✅ Updated ticket {d_id} | Fields: {list(fields.keys())} | Actor: {actor}")
                        
                        # Log Assignment Change if changed
                        if 'assigned_to' in fields and t_id:
                             new_assignment = fields['assigned_to']
                             if str(old_assignment) != str(new_assignment):
                                 self.log_ticket_event(t_id, 'assigned', actor or 'system', {
                                     'from': old_assignment, 
                                     'to': new_assignment
                                 }, cur=cur)
                        
                        # Log Status Change if changed
                        if 'status' in fields and t_id:
                             new_status = fields['status']
                             if str(old_status) != str(new_status):
                                 event_type = 'closed' if new_status.lower() == 'closed' else 'status_changed'
                                 self.log_ticket_event(t_id, event_type, actor or 'system', {
                                     'from': old_status, 
                                     'to': new_status
                                 }, cur=cur)
                        
                        # Log Draft Update if changed
                        if 'ai_draft' in fields and t_id:
                            self.log_audit_event(
                                ticket_id=t_id,
                                action_type=ActionType.AI_DRAFT_UPDATED,
                                actor_type=ActorType.USER if actor != 'system' else ActorType.SYSTEM,
                                actor_id=actor,
                                description=f"Draft response updated by {actor}.",
                                cur=cur
                            )
                                 
                        return True
                    else:
                        logger.warning(f"⚠️  No ticket found with identifier {identifier}")
                        return False
            
        except Exception as e:
            logger.error(f"❌ Error updating ticket identifier {identifier}: {e}")
            return False

    def log_response_sent(self, ticket_id: str, actor: str, recipient: str = None) -> bool:
        """
        Log that a response was sent to the customer.
        """
        return self.log_audit_event(
            ticket_id=ticket_id,
            action_type=ActionType.MESSAGE_SENT,
            actor_type=ActorType.USER,
            actor_id=actor,
            description=f"Response sent to {recipient or 'customer'} by {actor}.",
            metadata={'recipient': recipient}
        )

    def get_staff_metrics(self, staff_username: str, time_range: str = 'all') -> Dict:
        """
        Get Assigned, Open, and Closed ticket counts for a specific staff member.
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    time_filter, params = self._get_time_filter(time_range)
                    
                    # Base query with advanced metrics join
                    query = """
                        SELECT 
                            COUNT(t.id) as assigned,
                            COUNT(t.id) FILTER (WHERE t.status IN ('Open', 'Pending Review', 'Pending', 'In Progress')) as open,
                            COUNT(t.id) FILTER (WHERE t.status IN ('Closed', 'Resolved', 'Completed')) as closed,
                            AVG(m.first_response_duration) / 60 as avg_frt,
                            AVG(m.total_resolution_duration) / 3600 as avg_res,
                            COALESCE(SUM(m.reopen_count), 0) as reopens
                        FROM tickets t
                        LEFT JOIN ticket_metrics m ON t.ticket_id = m.ticket_id
                        WHERE t.assigned_to = %s
                        AND t.deleted_at IS NULL
                    """
                    
                    if time_filter:
                        query += f" AND t.{time_filter}"
                    
                    cur.execute(query, [staff_username] + params)
                    row = cur.fetchone()
                    
                    return {
                        'username': staff_username,
                        'assigned': row[0] or 0,
                        'open': row[1] or 0,
                        'closed': row[2] or 0,
                        'avg_frt': round(row[3] or 0, 1),
                        'avg_res': round(row[4] or 0, 1),
                        'reopens': int(row[5] or 0)
                    }
        except Exception as e:
            logger.error(f"❌ Error getting staff metrics for {staff_username}: {e}")
            return {'username': staff_username, 'assigned': 0, 'open': 0, 'closed': 0}

    def get_all_staff_metrics(self, staff_usernames: List[str], time_range: str = 'all') -> List[Dict]:
        """
        Get metrics for multiple staff members.
        """
        metrics = []
        for username in staff_usernames:
            metrics.append(self.get_staff_metrics(username, time_range))
        return metrics

    def get_client_stats(self, time_range: str = 'all') -> List[Dict]:
        """
        Get ticket statistics grouped by customer domain.
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    time_filter, params = self._get_time_filter(time_range)
                    
                    query = """
                        SELECT 
                            split_part(t.customer_email, '@', 2) as domain,
                            COUNT(t.id) as total,
                            COUNT(t.id) FILTER (WHERE t.status = 'Open') as open,
                            COUNT(t.id) FILTER (WHERE t.status = 'Closed') as closed,
                            MAX(t.last_updated) as last_activity,
                            AVG(m.first_response_duration) / 60 as avg_frt,
                            AVG(m.total_resolution_duration) / 3600 as avg_res
                        FROM tickets t
                        LEFT JOIN ticket_metrics m ON t.ticket_id = m.ticket_id
                        WHERE t.deleted_at IS NULL AND t.customer_email LIKE '%%@%%'
                    """
                    
                    if time_filter:
                        query += f" AND t.{time_filter}"
                    
                    query += " GROUP BY domain ORDER BY total DESC"
                    
                    cur.execute(query, params)
                    rows = cur.fetchall()
                    
                    return [{
                        'domain': row[0],
                        'total_tickets': row[1],
                        'open_tickets': row[2],
                        'closed_tickets': row[3],
                        'last_activity': row[4].isoformat() if row[4] else None,
                        'avg_frt': round(row[5] or 0, 1),
                        'avg_res': round(row[6] or 0, 1)
                    } for row in rows]
        except Exception as e:
            logger.error(f"❌ Error getting client stats: {e}")
            return []

    def get_ticket_timeline(self, ticket_id: str) -> List[Dict]:
        """
        Get all lifecycle events for a ticket from the structured audit log.
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    if ticket_id == 'GLOBAL':
                        cur.execute("""
                            SELECT a.*, t.display_id 
                            FROM ticket_audit_logs a
                            LEFT JOIN tickets t ON a.ticket_id = t.ticket_id
                            ORDER BY a.timestamp DESC 
                            LIMIT 100
                        """)
                    else:
                        cur.execute("""
                            SELECT a.*, t.display_id 
                            FROM ticket_audit_logs a
                            LEFT JOIN tickets t ON a.ticket_id = t.ticket_id
                            WHERE a.ticket_id = %s 
                            ORDER BY a.timestamp ASC
                        """, (ticket_id,))
                    
                    rows = cur.fetchall()
                    events = []
                    for row in rows:
                        event = dict(row)
                        ts = event['timestamp']
                        if isinstance(ts, datetime):
                            event['timestamp'] = ts.isoformat()
                            ts_str = ts.strftime('%Y-%m-%d %H:%M:%S')
                        else:
                            ts_str = str(ts)

                        actor = event.get('actor_id', 'unknown')
                        atype = event.get('action_type', '')
                        details = event.get('metadata') or {}
                        
                        # Generate human-readable description if not present or needs formatting
                        if event.get('description'):
                            event['event_text'] = event['description']
                        else:
                            if atype == ActionType.TICKET_CREATED:
                                event['event_text'] = f"Ticket created by {actor}."
                            elif atype == ActionType.ASSIGNMENT_CHANGED:
                                event['event_text'] = f"Ticket assigned to {details.get('to', 'someone')}."
                            elif atype == ActionType.STATUS_CHANGED:
                                event['event_text'] = f"Status changed to {details.get('to', 'Closed')} by {actor}."
                            elif atype == ActionType.MESSAGE_RECEIVED:
                                event['event_text'] = f"New message from customer {actor}."
                            elif atype == ActionType.MESSAGE_SENT:
                                event['event_text'] = f"Reply sent by {actor}."
                            elif atype == ActionType.AI_DRAFT_GENERATED:
                                event['event_text'] = f"AI generated a draft response."
                            else:
                                event['event_text'] = f"{actor} performed {atype}."
                            
                        events.append(event)
                    return events
        except Exception as e:
            logger.error(f"❌ Error fetching ticket timeline for {ticket_id}: {e}")
            return []

    def _get_time_filter(self, time_range: str) -> tuple[str, list]:
        """Helper to generate SQL time filter and params"""
        now = datetime.now()
        if time_range == 'today':
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return "created_at >= %s", [start]
        elif time_range == 'week':
            start = now - timedelta(days=now.weekday())
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            return "created_at >= %s", [start]
        elif time_range == 'month':
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            return "created_at >= %s", [start]
        elif time_range == 'year':
            start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            return "created_at >= %s", [start]
        else:
            return "", []
    
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

    def get_all_contact_emails(self) -> List[str]:
        """Get unique list of all emails from tickets and messages."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT DISTINCT email FROM (
                            SELECT customer_email as email FROM tickets WHERE customer_email IS NOT NULL AND customer_email != ''
                            UNION
                            SELECT sender as email FROM ticket_messages WHERE sender IS NOT NULL AND sender != ''
                        ) AS all_emails
                    """)
                    rows = cur.fetchall()
                    return [row[0] for row in rows]
        except Exception as e:
            logger.error(f"❌ Error fetching all contact emails: {e}")
            return []
    
    def get_agent_performance(self) -> List[Dict]:
        """
        Get aggregated performance metrics for all agents.
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT 
                            assigned_agent as agent,
                            COUNT(*) as total_tickets,
                            COUNT(*) FILTER (WHERE resolved_at IS NOT NULL) as resolved,
                            AVG(first_response_duration) / 60 as avg_frt_minutes,
                            AVG(total_resolution_duration) / 3600 as avg_resolution_hours,
                            SUM(reopen_count) as total_reopens
                        FROM ticket_metrics
                        WHERE assigned_agent IS NOT NULL AND assigned_agent != 'Unassigned'
                        GROUP BY assigned_agent
                        ORDER BY total_tickets DESC
                    """)
                    return cur.fetchall()
        except Exception as e:
            logger.error(f"❌ Error fetching agent performance: {e}")
            return []

    def get_weekly_analytics_summary(self) -> Dict:
        """
        Get high-level summary for the weekly report.
        """
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    # Metrics for last 7 days
                    cur.execute("""
                        SELECT 
                            COUNT(*) as tickets_created,
                            COUNT(*) FILTER (WHERE resolved_at IS NOT NULL) as tickets_resolved,
                            AVG(first_response_duration) / 60 as avg_frt_mins,
                            AVG(total_resolution_duration) / 3600 as avg_res_hours,
                            (COUNT(*) FILTER (WHERE first_response_duration < 14400))::FLOAT / 
                                NULLIF(COUNT(*), 0) * 100 as sla_compliance_pct
                        FROM ticket_metrics
                        WHERE created_at >= NOW() - INTERVAL '7 days'
                    """)
                    return dict(cur.fetchone()) if cur.rowcount > 0 else {}
        except Exception as e:
            logger.error(f"❌ Error fetching weekly summary: {e}")
            return {}

    def close(self):
        """Close all database connections"""
        self.conn_manager.close_all()