"""
Ticket and Message Repository for PostgreSQL database.
Handles schema initialization, CRUD operations, lifecycle management (soft/hard deletion),
AI regeneration flags, attachment context, client groups, and Azure DevOps work item links.
"""

# Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Ticket repository and CRUD operations module - start
import json
import logging
from datetime import datetime
from typing import List, Dict, Optional, Union
from psycopg2 import extras, sql
from modules.db_connection import ActionType, ActorType, PostgreSQLConnectionManager

logger = logging.getLogger(__name__)


class TicketRepository:
    """
    Repository layer for ticket and message storage, schema management,
    and database querying.
    """
    
    def __init__(self, conn_manager: Optional[PostgreSQLConnectionManager] = None):
        """
        Initialize the TicketRepository service with a PostgreSQL connection manager.
        
        Args:
            conn_manager: Shared database connection pool manager instance.
        """
        self.conn_manager = conn_manager or PostgreSQLConnectionManager()

    # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Default audit logging stubs for standalone repository usage - start
    def log_audit_event(self, ticket_id: str, action_type: str, actor_id: str, 
                        actor_type: str = ActorType.SYSTEM, description: str = None, 
                        metadata: dict = None, cur = None, timestamp: datetime = None) -> bool:
        """
        Fallback audit event logger stub for standalone repository usage.
        Overridden by TicketAnalytics implementation when composed via SQLLogger.
        """
        logger.debug(f"Audit event stub [{action_type}] on ticket {ticket_id} by {actor_id}")
        return True

    def log_ticket_event(self, ticket_id: str, event_type: str, actor: str, details: dict = None, cur = None) -> bool:
        """
        Fallback ticket event logger stub for standalone repository usage.
        Overridden by TicketAnalytics implementation when composed via SQLLogger.
        """
        logger.debug(f"Ticket event stub [{event_type}] on ticket {ticket_id} by {actor}")
        return True
    # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Default audit logging stubs for standalone repository usage - end

    def authenticate(self) -> bool:
        """
        Initialize tables with relational schema, provenance storage, and migrations.
        
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

                            -- Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Add devops_work_item_ids column migration - start
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                           WHERE table_name='tickets' AND column_name='devops_work_item_ids') THEN 
                                ALTER TABLE tickets ADD COLUMN devops_work_item_ids JSONB DEFAULT '[]'::jsonb;
                            END IF;
                            -- Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Add devops_work_item_ids column migration - end
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
                    
                    # 3. ATTACHMENT CONTEXT TABLE
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
                    
                    # Soft-delete indexes
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

                    # 6. AUDIT LOGS TABLE
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

                    # 7. TICKET METRICS TABLE
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
                    
                    # 8. HOLIDAYS TABLE
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS holidays (
                            holiday_date DATE PRIMARY KEY,
                            holiday_name TEXT NOT NULL,
                            created_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """)
                    
                    # 9. CLIENT GROUPS TABLE
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS client_groups (
                            id SERIAL PRIMARY KEY,
                            name TEXT UNIQUE NOT NULL,
                            domains TEXT NOT NULL,
                            created_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """)
                    
            logger.info("✅ PostgreSQL schema initialized (with provenance + soft-delete support)")
            return True
            
        except Exception as e:
            logger.error(f"❌ Schema initialization failed: {e}")
            return False

    def create_ticket(self, ticket_id: str, conversation_id: str, subject: str, 
                      customer_email: str, actor: str = 'system', source_received_at: Union[datetime, str] = None) -> bool:
        """Create a new ticket (idempotent)."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    now = datetime.now()
                    
                    parsed_source_time = source_received_at
                    if isinstance(parsed_source_time, str):
                        try:
                            if parsed_source_time.endswith('Z'):
                                parsed_source_time = parsed_source_time.replace('Z', '+00:00')
                            parsed_source_time = datetime.fromisoformat(parsed_source_time)
                        # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Fix bare except on datetime parse
                        except (ValueError, TypeError):
                            parsed_source_time = None

                    domain = customer_email.split('@')[-1].split('.')[0].upper()
                    date_str = now.strftime('%m%d')
                    
                    cur.execute("""
                        SELECT COUNT(*) FROM tickets 
                        WHERE customer_email LIKE %s 
                        AND created_at >= %s
                    """, (f'%@{customer_email.split("@")[-1]}', now.replace(hour=0, minute=0, second=0, microsecond=0)))
                    seq = cur.fetchone()[0] + 1
                    display_id = f"{domain}-{date_str}-{seq:02d}"
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
                        self.log_audit_event(
                            ticket_id=ticket_id,
                            action_type=ActionType.TICKET_CREATED,
                            actor_type=ActorType.USER if actor != 'system' else ActorType.SYSTEM,
                            actor_id=actor,
                            description=f"Ticket created in system.",
                            cur=cur,
                            timestamp=parsed_source_time or now
                        )
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
                        display_id = f"{domain}-{date_str}-X{row_id}"
                        cur.execute("UPDATE tickets SET display_id = %s WHERE id = %s", (display_id, row_id))
                    logger.info(f"✅ Backfilled display_id for {len(rows)} tickets")
        except Exception as e:
            logger.error(f"❌ Error backfilling display_ids: {e}")

    def get_display_id(self, ticket_id: str) -> str:
        """Get the human-readable display ID for a ticket."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT display_id FROM tickets WHERE ticket_id = %s", (ticket_id,))
                    row = cur.fetchone()
                    return row[0] if (row and row[0]) else ticket_id
        except Exception as e:
            logger.error(f"❌ Error fetching display_id for {ticket_id}: {e}")
            return ticket_id

    def update_ai_draft(self, ticket_id: str, draft: str, provenance: Optional[List[Dict]] = None) -> bool:
        """Update the AI draft for a ticket, optionally storing RAG provenance metadata."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    provenance_json = json.dumps(provenance) if provenance else None
                    cur.execute("""
                        UPDATE tickets 
                        SET ai_draft = %s, rag_provenance = %s, last_updated = %s
                        WHERE ticket_id = %s
                    """, (draft, provenance_json, datetime.now(), ticket_id))
                    
                    if cur.rowcount > 0:
                        disp_query = sql.SQL("SELECT display_id FROM tickets WHERE ticket_id = %s")
                        cur.execute(disp_query, (ticket_id,))
                        d_row = cur.fetchone()
                        d_id = d_row[0] if d_row else ticket_id
                        logger.info(f"✅ Updated AI draft for ticket {d_id}")
                        self.log_audit_event(
                            ticket_id=ticket_id,
                            action_type=ActionType.AI_DRAFT_GENERATED,
                            actor_type=ActorType.AI,
                            actor_id='system',
                            description="AI draft regenerated.",
                            metadata={'provenance_count': len(provenance) if provenance else 0},
                            cur=cur
                        )
                        return True
                    return False
        except Exception as e:
            logger.error(f"❌ Error updating AI draft for ticket {ticket_id}: {e}")
            return False

    def flag_for_ai_regeneration(self, ticket_id: str) -> bool:
        """Flag a ticket as needing AI response generation."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE tickets 
                        SET needs_ai_generation = TRUE, last_updated = %s
                        WHERE ticket_id = %s
                    """, (datetime.now(), ticket_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error(f"❌ Error flagging ticket {ticket_id} for AI generation: {e}")
            return False

    def mark_ticket_as_read(self, ticket_id: str) -> bool:
        """Mark a ticket as read by clearing has_unread_response."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
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
        """Get all ticket_ids that have been flagged as needing AI generation."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT ticket_id 
                        FROM tickets 
                        WHERE needs_ai_generation = TRUE 
                          AND (status != 'Ignore' OR status IS NULL)
                          AND deleted_at IS NULL
                    """)
                    rows = cur.fetchall()
                    return [r[0] for r in rows]
        except Exception as e:
            logger.error(f"❌ Error getting tickets needing regeneration: {e}")
            return []

    def clear_ai_regeneration_flags(self, ticket_ids: List[str]) -> bool:
        """Clear the needs_ai_generation flag for the specified ticket IDs."""
        if not ticket_ids:
            return True
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE tickets 
                        SET needs_ai_generation = FALSE
                        WHERE ticket_id = ANY(%s)
                    """, (ticket_ids,))
                    logger.info(f"✅ Cleared AI generation flags for {cur.rowcount} tickets")
                    return True
        except Exception as e:
            logger.error(f"❌ Error clearing AI generation flags: {e}")
            return False

    def update_ticket_issue_state(self, ticket_id: str, issue_state: Dict) -> bool:
        """Persist structured issue state (status, components, impact) for a ticket."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE tickets 
                        SET issue_state = %s, last_updated = %s
                        WHERE ticket_id = %s
                    """, (json.dumps(issue_state), datetime.now(), ticket_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error(f"❌ Error updating issue state for ticket {ticket_id}: {e}")
            return False

    def get_ticket_issue_state(self, ticket_id: str) -> Optional[Dict]:
        """Retrieve structured issue state for a ticket."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT issue_state FROM tickets WHERE ticket_id = %s", (ticket_id,))
                    row = cur.fetchone()
                    if row and row[0]:
                        return row[0] if isinstance(row[0], dict) else json.loads(row[0])
                    return None
        except Exception as e:
            logger.error(f"❌ Error fetching issue state for ticket {ticket_id}: {e}")
            return None

    def mark_email_as_failed_processing(self, message_id: str, error_msg: str) -> bool:
        """Mark an email message as having failed ingestion/processing."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE ticket_messages 
                        SET body_text = body_text || %s
                        WHERE message_id = %s
                    """, (f"\n\n[PROCESSING WARNING: {error_msg}]", message_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error(f"❌ Error marking email {message_id} as failed: {e}")
            return False

    def get_client_groups(self) -> List[Dict]:
        """Retrieve all client entity groups."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("SELECT * FROM client_groups ORDER BY name ASC")
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"❌ Error fetching client groups: {e}")
            return []

    def create_client_group(self, name: str, domains: str) -> bool:
        """Create a new client group mapping multiple domains under a single organization."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO client_groups (name, domains) VALUES (%s, %s)", (name.strip(), domains.strip()))
                    return True
        except Exception as e:
            logger.error(f"❌ Error creating client group {name}: {e}")
            return False

    def delete_client_group(self, group_id: int) -> bool:
        """Delete a client entity group."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM client_groups WHERE id = %s", (group_id,))
                    return True
        except Exception as e:
            logger.error(f"❌ Error deleting client group {group_id}: {e}")
            return False

    def get_all_client_entities(self) -> List[Dict]:
        """Get list of all custom merged client entities and unmerged raw domains."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("SELECT id, name, domains FROM client_groups ORDER BY name ASC")
                    groups = [dict(r) for r in cur.fetchall()]
                    
                    cur.execute("""
                        SELECT DISTINCT split_part(customer_email, '@', 2) as raw_domain 
                        FROM tickets 
                        WHERE customer_email LIKE '%%@%%'
                    """)
                    all_domains = [r['raw_domain'] for r in cur.fetchall() if r['raw_domain']]
                    
                    merged_domains = set()
                    for g in groups:
                        doms = [d.strip() for d in g['domains'].split(',') if d.strip()]
                        merged_domains.update(doms)
                        g['domain_list'] = doms
                    
                    unmerged = [d for d in all_domains if d not in merged_domains]
                    return {'groups': groups, 'unmerged_domains': unmerged}
        except Exception as e:
            logger.error(f"❌ Error getting client entities: {e}")
            return {'groups': [], 'unmerged_domains': []}

    def merge_client_entities(self, new_name: str, domains_to_merge: List[str]) -> bool:
        """Merge multiple domain entities into a unified organization."""
        try:
            cleaned_domains = list(set([d.strip() for d in domains_to_merge if d.strip()]))
            if not cleaned_domains:
                return False
            domains_str = ", ".join(cleaned_domains)
            
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO client_groups (name, domains) 
                        VALUES (%s, %s)
                        ON CONFLICT (name) DO UPDATE SET domains = EXCLUDED.domains
                    """, (new_name.strip(), domains_str))
                    return True
        except Exception as e:
            logger.error(f"❌ Error merging client entities: {e}")
            return False

    def store_attachment_context(self, ticket_id: str, message_id: str, filename: str, 
                                 content_summary: str, metadata: Optional[Dict] = None) -> bool:
        """Store extracted OCR/VLM text summary for an email attachment."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO ticket_attachments_context (ticket_id, message_id, filename, content_summary, metadata)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (ticket_id, message_id, filename, content_summary, json.dumps(metadata) if metadata else None))
                    return True
        except Exception as e:
            logger.error(f"❌ Error storing attachment context: {e}")
            return False

    def get_ticket_attachments_context(self, ticket_id: str) -> List[Dict]:
        """Get all attachment summaries linked to a ticket."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT * FROM ticket_attachments_context 
                        WHERE ticket_id = %s 
                        ORDER BY created_at ASC
                    """, (ticket_id,))
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"❌ Error fetching attachment context for {ticket_id}: {e}")
            return []

    def mark_ticket_as_authority(self, ticket_id: str, is_authority: bool = True) -> bool:
        """Mark a ticket as an authoritative reference in knowledge retrieval."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE tickets 
                        SET is_authority = %s, last_updated = %s
                        WHERE ticket_id = %s
                    """, (is_authority, datetime.now(), ticket_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error(f"❌ Error marking ticket {ticket_id} as authority: {e}")
            return False

    def soft_delete_ticket(self, ticket_id: str) -> bool:
        """Soft delete a ticket by setting deleted_at timestamp."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    now = datetime.now()
                    cur.execute("""
                        UPDATE tickets 
                        SET deleted_at = %s, last_updated = %s
                        WHERE ticket_id = %s AND deleted_at IS NULL
                    """, (now, now, ticket_id))
                    cur.execute("""
                        UPDATE ticket_messages 
                        SET deleted_at = %s
                        WHERE ticket_id = %s AND deleted_at IS NULL
                    """, (now, ticket_id))
                    
                    if cur.rowcount > 0:
                        logger.info(f"🗑️ Soft-deleted ticket {ticket_id}")
                        self.log_audit_event(
                            ticket_id=ticket_id,
                            action_type=ActionType.TICKET_DELETED,
                            actor_type=ActorType.SYSTEM,
                            actor_id='system',
                            description="Ticket soft-deleted.",
                            cur=cur
                        )
                        return True
                    return False
        except Exception as e:
            logger.error(f"❌ Error soft-deleting ticket {ticket_id}: {e}")
            return False

    def reopen_ticket(self, ticket_id: str) -> bool:
        """Restore a soft-deleted ticket and mark reopened."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    now = datetime.now()
                    cur.execute("""
                        UPDATE tickets 
                        SET deleted_at = NULL, status = 'Open', reopened = TRUE, 
                            last_updated = %s, has_unread_response = TRUE
                        WHERE ticket_id = %s
                    """, (now, ticket_id))
                    cur.execute("""
                        UPDATE ticket_messages 
                        SET deleted_at = NULL
                        WHERE ticket_id = %s
                    """, (ticket_id,))
                    
                    if cur.rowcount > 0:
                        logger.info(f"🔄 Reopened ticket {ticket_id}")
                        self.log_audit_event(
                            ticket_id=ticket_id,
                            action_type=ActionType.TICKET_RESTORED,
                            actor_type=ActorType.USER,
                            actor_id='customer',
                            description="Ticket reopened via incoming message.",
                            cur=cur
                        )
                        return True
                    return False
        except Exception as e:
            logger.error(f"❌ Error reopening ticket {ticket_id}: {e}")
            return False

    def soft_delete_message(self, message_id: str) -> bool:
        """Soft delete a specific message within a ticket."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE ticket_messages 
                        SET deleted_at = %s
                        WHERE message_id = %s AND deleted_at IS NULL
                    """, (datetime.now(), message_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error(f"❌ Error soft-deleting message {message_id}: {e}")
            return False

    def get_deleted_tickets(self, since: Optional[datetime] = None) -> List[str]:
        """Get ticket_ids that were soft-deleted."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    if since:
                        cur.execute("SELECT ticket_id FROM tickets WHERE deleted_at >= %s", (since,))
                    else:
                        cur.execute("SELECT ticket_id FROM tickets WHERE deleted_at IS NOT NULL")
                    return [r[0] for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"❌ Error getting deleted tickets: {e}")
            return []

    def get_deleted_messages(self, since: Optional[datetime] = None) -> List[str]:
        """Get message_ids that were soft-deleted."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    if since:
                        cur.execute("SELECT message_id FROM ticket_messages WHERE deleted_at >= %s", (since,))
                    else:
                        cur.execute("SELECT message_id FROM ticket_messages WHERE deleted_at IS NOT NULL")
                    return [r[0] for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"❌ Error getting deleted messages: {e}")
            return []

    def get_soft_delete_candidates(self, cutoff_dt: datetime, limit: int = 100) -> List[str]:
        """Find closed tickets inactive since cutoff_dt that are candidates for soft-delete."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT ticket_id FROM tickets 
                        WHERE status IN ('Closed', 'Resolved') 
                          AND last_updated < %s 
                          AND deleted_at IS NULL
                        ORDER BY last_updated ASC
                        LIMIT %s
                    """, (cutoff_dt, limit))
                    return [r[0] for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"❌ Error getting soft-delete candidates: {e}")
            return []

    def get_hard_delete_candidates(self, cutoff_dt: datetime, limit: int = 100) -> List[str]:
        """Find soft-deleted tickets that have exceeded retention period for hard purge."""
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
                    return [r[0] for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"❌ Error getting hard-delete candidates: {e}")
            return []

    def get_message_ids_for_ticket(self, ticket_id: str) -> List[str]:
        """Get all message IDs associated with a ticket."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT message_id FROM ticket_messages WHERE ticket_id = %s", (ticket_id,))
                    return [r[0] for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"❌ Error getting message IDs for ticket {ticket_id}: {e}")
            return []

    def hard_delete_tickets(self, ticket_ids: List[str]) -> int:
        """Permanently remove tickets and cascade to messages and audit logs."""
        if not ticket_ids:
            return 0
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM tickets WHERE ticket_id = ANY(%s)", (ticket_ids,))
                    count = cur.rowcount
                    logger.info(f"🔥 Hard-deleted {count} tickets from SQL")
                    return count
        except Exception as e:
            logger.error(f"❌ Error hard-deleting tickets: {e}")
            return 0

    def purge_heavy_ticket_data(self, ticket_ids: List[str]) -> int:
        """Purge message bodies and HTML to free space while keeping metadata rows."""
        if not ticket_ids:
            return 0
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE ticket_messages 
                        SET body_text = '[PURGED]', body_html = NULL, attachments = '[]'
                        WHERE ticket_id = ANY(%s)
                    """, (ticket_ids,))
                    cur.execute("""
                        UPDATE tickets 
                        SET ai_draft = NULL, rag_provenance = NULL, issue_state = NULL
                        WHERE ticket_id = ANY(%s)
                    """, (ticket_ids,))
                    return len(ticket_ids)
        except Exception as e:
            logger.error(f"❌ Error purging heavy ticket data: {e}")
            return 0

    def find_ticket_by_conversation_id(self, conversation_id: str) -> Optional[Dict]:
        """Find a ticket by Graph API conversation ID."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("SELECT * FROM tickets WHERE conversation_id = %s", (conversation_id,))
                    row = cur.fetchone()
                    return self._convert_ticket_row(dict(row)) if row else None
        except Exception as e:
            logger.error(f"❌ Error finding ticket by conversation ID {conversation_id}: {e}")
            return None

    def message_exists(self, message_id: str, internet_message_id: Optional[str] = None) -> bool:
        """Check if an email message is already logged."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    if internet_message_id:
                        cur.execute("""
                            SELECT 1 FROM ticket_messages 
                            WHERE message_id = %s OR internet_message_id = %s 
                            LIMIT 1
                        """, (message_id, internet_message_id))
                    else:
                        cur.execute("SELECT 1 FROM ticket_messages WHERE message_id = %s LIMIT 1", (message_id,))
                    return bool(cur.fetchone())
        except Exception as e:
            logger.error(f"❌ Error checking message existence {message_id}: {e}")
            return False

    def get_ticket_by_id(self, ticket_id: str) -> Optional[Dict]:
        """Retrieve ticket details by internal ticket UUID or display ID."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT * FROM tickets 
                        WHERE ticket_id = %s OR display_id = %s
                    """, (ticket_id, ticket_id))
                    row = cur.fetchone()
                    return self._convert_ticket_row(dict(row)) if row else None
        except Exception as e:
            logger.error(f"❌ Error getting ticket by ID {ticket_id}: {e}")
            return None

    def get_thread_messages(self, ticket_id: str) -> List[Dict]:
        """Get all chronological messages in a ticket thread."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT * FROM ticket_messages 
                        WHERE ticket_id = %s AND deleted_at IS NULL
                        ORDER BY timestamp ASC
                    """, (ticket_id,))
                    return [self._convert_message_row(dict(r)) for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"❌ Error getting thread messages for {ticket_id}: {e}")
            return []

    def get_least_busy_staff(self) -> str:
        """Get the username of the staff member with fewest active tickets."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT assigned_to, COUNT(*) as active_count
                        FROM tickets
                        WHERE status IN ('Open', 'Pending', 'In Progress')
                          AND assigned_to != 'Unassigned'
                          AND assigned_to IS NOT NULL
                          AND deleted_at IS NULL
                        GROUP BY assigned_to
                        ORDER BY active_count ASC
                        LIMIT 1
                    """)
                    row = cur.fetchone()
                    return row[0] if row else 'Unassigned'
        except Exception as e:
            logger.error(f"❌ Error finding least busy staff: {e}")
            return 'Unassigned'

    def get_active_tickets(self, assigned_to: Optional[str] = None) -> List[Dict]:
        """Get active, non-deleted tickets."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    query = """
                        SELECT t.*, 
                               MAX(m.timestamp) as last_message_at,
                               COUNT(m.id) as message_count
                        FROM tickets t
                        LEFT JOIN ticket_messages m ON t.ticket_id = m.ticket_id AND m.deleted_at IS NULL
                        WHERE t.deleted_at IS NULL
                    """
                    params = []
                    if assigned_to:
                        query += " AND t.assigned_to = %s"
                        params.append(assigned_to)
                        
                    query += " GROUP BY t.id ORDER BY t.last_updated DESC"
                    cur.execute(query, params)
                    return [self._convert_ticket_row(dict(r)) for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"❌ Error fetching active tickets: {e}")
            return []

    def search_tickets(self, query: str) -> List[Dict]:
        """Search tickets by subject, customer email, display ID, or body text."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    search_term = f"%{query}%"
                    cur.execute("""
                        SELECT DISTINCT t.* 
                        FROM tickets t
                        LEFT JOIN ticket_messages m ON t.ticket_id = m.ticket_id
                        WHERE (t.subject ILIKE %s 
                            OR t.customer_email ILIKE %s 
                            OR t.display_id ILIKE %s 
                            OR t.ticket_id ILIKE %s 
                            OR m.body_text ILIKE %s)
                          AND t.deleted_at IS NULL
                        ORDER BY t.last_updated DESC
                        LIMIT 50
                    """, (search_term, search_term, search_term, search_term, search_term))
                    return [self._convert_ticket_row(dict(r)) for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"❌ Error searching tickets: {e}")
            return []

    def update_ticket_fields(self, identifier: Union[int, str], fields: dict, actor: str = 'system') -> bool:
        """Dynamically update ticket attributes safely with parameterized identifiers."""
        if not fields:
            return False
            
        try:
            where_col = "id" if isinstance(identifier, int) else "ticket_id"
            
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    fields['last_updated'] = datetime.now()
                    
                    set_parts = []
                    values = []
                    for k, v in fields.items():
                        set_parts.append(sql.Identifier(k))
                        if isinstance(v, (dict, list)):
                            values.append(json.dumps(v))
                        else:
                            values.append(v)
                            
                    old_assignment = None
                    old_status = None
                    t_id = identifier if where_col == "ticket_id" else None
                    
                    # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Parameterized SQL identifier formatting - start
                    if 'assigned_to' in fields or 'status' in fields:
                        check_query = sql.SQL("SELECT ticket_id, assigned_to, status FROM tickets WHERE {} = %s").format(sql.Identifier(where_col))
                        cur.execute(check_query, (identifier,))
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
                        disp_query = sql.SQL("SELECT display_id FROM tickets WHERE {} = %s").format(sql.Identifier(where_col))
                        cur.execute(disp_query, (identifier,))
                        d_row = cur.fetchone()
                        d_id = d_row[0] if d_row else identifier
                    # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Parameterized SQL identifier formatting - end
                        
                        logger.info(f"✅ Updated ticket {d_id} | Fields: {list(fields.keys())} | Actor: {actor}")
                        
                        if 'assigned_to' in fields and t_id:
                            new_assignment = fields['assigned_to']
                            if str(old_assignment) != str(new_assignment):
                                self.log_ticket_event(t_id, 'assigned', actor or 'system', {
                                    'from': old_assignment, 
                                    'to': new_assignment
                                }, cur=cur)

                        if 'status' in fields and t_id:
                            new_status = fields['status']
                            if str(old_status) != str(new_status):
                                self.log_ticket_event(t_id, 'status_changed', actor or 'system', {
                                    'from': old_status, 
                                    'to': new_status
                                }, cur=cur)

                        return True
                    return False
        except Exception as e:
            logger.error(f"❌ Error updating ticket fields for {identifier}: {e}")
            return False

    def log_message(self, ticket_id: str, email: dict, is_internal: bool = False) -> bool:
        """Log a message under a ticket and trigger unread / SLA update events."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    now = datetime.now()
                    msg_timestamp = email.get('received_at', now)
                    
                    if isinstance(msg_timestamp, str):
                        try:
                            if msg_timestamp.endswith('Z'):
                                msg_timestamp = msg_timestamp.replace('Z', '+00:00')
                            msg_timestamp = datetime.fromisoformat(msg_timestamp)
                        except (ValueError, TypeError):
                            msg_timestamp = now

                    attachments = email.get('attachments', [])
                    attachments_json = json.dumps(attachments) if isinstance(attachments, list) else attachments

                    cur.execute("""
                        INSERT INTO ticket_messages (
                            ticket_id, message_id, internet_message_id, sender, 
                            to_email, body_text, body_html, timestamp, 
                            attachments, cc, bcc, is_internal
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (message_id) DO NOTHING
                    """, (
                        ticket_id, email.get('id'), email.get('internet_message_id'),
                        email.get('sender'), email.get('to_email', ''),
                        email.get('body'), email.get('body_html', ''),
                        msg_timestamp, attachments_json,
                        email.get('cc', ''), email.get('bcc', ''), is_internal
                    ))
                    
                    inserted = cur.rowcount > 0
                    if inserted:
                        if not is_internal:
                            cur.execute("""
                                UPDATE tickets 
                                SET last_updated = %s, has_unread_response = TRUE
                                WHERE ticket_id = %s
                            """, (now, ticket_id))
                        else:
                            cur.execute("""
                                UPDATE tickets 
                                SET last_updated = %s
                                WHERE ticket_id = %s
                            """, (now, ticket_id))

                        event_action = ActionType.NOTE_ADDED if is_internal else ActionType.MESSAGE_RECEIVED
                        actor_type = ActorType.USER if is_internal else ActorType.USER
                        self.log_audit_event(
                            ticket_id=ticket_id,
                            action_type=event_action,
                            actor_type=actor_type,
                            actor_id=email.get('sender', 'unknown'),
                            description=f"Message logged: {email.get('subject', '')[:40]}",
                            cur=cur,
                            timestamp=msg_timestamp
                        )
                        return True
                    return False
        except Exception as e:
            logger.error(f"❌ Error logging message for ticket {ticket_id}: {e}")
            return False

    def get_ticket_devops_work_items(self, ticket_id: str) -> List[int]:
        """Get list of linked Azure DevOps work item IDs for a ticket."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT devops_work_item_ids FROM tickets WHERE ticket_id = %s OR display_id = %s", (ticket_id, ticket_id))
                    row = cur.fetchone()
                    if row and row[0]:
                        raw = row[0]
                        if isinstance(raw, str):
                            raw = json.loads(raw)
                        if isinstance(raw, list):
                            ids = []
                            for x in raw:
                                if isinstance(x, dict) and 'id' in x:
                                    ids.append(int(x['id']))
                                elif str(x).isdigit():
                                    ids.append(int(x))
                            return ids
                    return []
        except Exception as e:
            logger.error(f"❌ Error fetching devops work items for ticket {ticket_id}: {e}")
            return []

    def get_all_linked_devops_work_items(self) -> List[Dict]:
        """Returns all currently active DevOps work item links across all tickets."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT ticket_id, devops_work_item_ids
                        FROM tickets
                        WHERE devops_work_item_ids IS NOT NULL 
                          AND devops_work_item_ids::text != '[]' 
                          AND devops_work_item_ids::text != 'null'
                          AND deleted_at IS NULL
                    """)
                    rows = cur.fetchall()
                    results = []
                    for row in rows:
                        t_id = row[0]
                        items = row[1]
                        if isinstance(items, str):
                            items = json.loads(items)
                        if isinstance(items, list):
                            for it in items:
                                if isinstance(it, dict) and 'id' in it:
                                    results.append({
                                        'ticket_id': t_id,
                                        'work_item_id': int(it['id']),
                                        'project': it.get('project') or ''
                                    })
                                elif str(it).isdigit():
                                    results.append({
                                        'ticket_id': t_id,
                                        'work_item_id': int(it),
                                        'project': ''
                                    })
                    return results
        except Exception as e:
            logger.error(f"❌ Error fetching all linked devops work items: {e}")
            return []

    def link_devops_work_item(self, ticket_id: str, work_item_id: int, actor: str = 'system', project: str = None) -> bool:
        """Link an Azure DevOps work item ID to a ticket and record audit log."""
        try:
            work_item_id = int(work_item_id)
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT ticket_id, devops_work_item_ids FROM tickets WHERE ticket_id = %s OR display_id = %s FOR UPDATE", (ticket_id, ticket_id))
                    row = cur.fetchone()
                    if not row:
                        return False
                    actual_ticket_id = row[0]
                    current_raw = row[1] or []
                    if isinstance(current_raw, str):
                        current_raw = json.loads(current_raw)
                    
                    existing_ids = []
                    for x in current_raw:
                        if isinstance(x, dict) and 'id' in x:
                            existing_ids.append(int(x['id']))
                        elif str(x).isdigit():
                            existing_ids.append(int(x))

                    if work_item_id not in existing_ids:
                        item_entry = {'id': work_item_id, 'project': project or ''}
                        current_raw.append(item_entry)
                        cur.execute(
                            "UPDATE tickets SET devops_work_item_ids = %s, last_updated = %s WHERE ticket_id = %s",
                            (json.dumps(current_raw), datetime.now(), actual_ticket_id)
                        )
                        self.log_audit_event(
                            ticket_id=actual_ticket_id,
                            action_type=ActionType.DEVOPS_ITEM_LINKED,
                            actor_type=ActorType.USER if actor != 'system' else ActorType.SYSTEM,
                            actor_id=actor,
                            description=f"Linked Azure DevOps Work Item #{work_item_id}" + (f" ({project})" if project else ""),
                            metadata={'work_item_id': work_item_id, 'project': project},
                            cur=cur
                        )
                    return True
        except Exception as e:
            logger.error(f"❌ Error linking devops work item #{work_item_id} to ticket {ticket_id}: {e}")
            return False

    def unlink_devops_work_item(self, ticket_id: str, work_item_id: int, actor: str = 'system') -> bool:
        """Unlink an Azure DevOps work item ID from a ticket and record audit log."""
        try:
            work_item_id = int(work_item_id)
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT ticket_id, devops_work_item_ids FROM tickets WHERE ticket_id = %s OR display_id = %s FOR UPDATE", (ticket_id, ticket_id))
                    row = cur.fetchone()
                    if not row:
                        return False
                    actual_ticket_id = row[0]
                    current_raw = row[1] or []
                    if isinstance(current_raw, str):
                        current_raw = json.loads(current_raw)
                    
                    new_list = []
                    found = False
                    for x in current_raw:
                        x_id = int(x['id']) if (isinstance(x, dict) and 'id' in x) else (int(x) if str(x).isdigit() else None)
                        if x_id == work_item_id:
                            found = True
                        else:
                            new_list.append(x)

                    if found:
                        cur.execute(
                            "UPDATE tickets SET devops_work_item_ids = %s, last_updated = %s WHERE ticket_id = %s",
                            (json.dumps(new_list), datetime.now(), actual_ticket_id)
                        )
                        self.log_audit_event(
                            ticket_id=actual_ticket_id,
                            action_type=ActionType.DEVOPS_ITEM_UNLINKED,
                            actor_type=ActorType.USER if actor != 'system' else ActorType.SYSTEM,
                            actor_id=actor,
                            description=f"Unlinked Azure DevOps Work Item #{work_item_id}",
                            metadata={'work_item_id': work_item_id},
                            cur=cur
                        )
                    return True
        except Exception as e:
            logger.error(f"❌ Error unlinking devops work item #{work_item_id} from ticket {ticket_id}: {e}")
            return False

    def _convert_ticket_row(self, row: Dict) -> Dict:
        """Convert PostgreSQL ticket row to expected format."""
        if not row:
            return row
        for field in ['created_at', 'last_updated', 'last_message_at', 'deleted_at']:
            if isinstance(row.get(field), datetime):
                row[field] = row[field].isoformat()
        if row.get('rag_provenance') and isinstance(row['rag_provenance'], str):
            try:
                row['rag_provenance'] = json.loads(row['rag_provenance'])
            except json.JSONDecodeError:
                row['rag_provenance'] = None
        return row

    def _convert_message_row(self, row: Dict) -> Dict:
        """Convert PostgreSQL message row to expected format."""
        if not row:
            return row
        for field in ['timestamp', 'deleted_at']:
            if isinstance(row.get(field), datetime):
                row[field] = row[field].isoformat()
        if 'is_internal' in row:
            row['is_internal'] = 1 if row['is_internal'] else 0
        if isinstance(row.get('attachments'), str):
            try:
                row['attachments'] = json.loads(row['attachments'])
            except json.JSONDecodeError:
                row['attachments'] = []
        return row
# Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Ticket repository and CRUD operations module - end
