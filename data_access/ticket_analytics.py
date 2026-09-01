"""
Ticket Analytics, SLA Metrics, Audit Logging, and Holiday Schedule Management.
Provides SLA duration tracking (business hours), staff/client analytics, and audit timelines.
"""

# Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Ticket analytics, audit logs, and metrics module - start
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Any, Union
import time
import hashlib
from psycopg2 import extras
from modules.db_connection import ActionType, ActorType, PostgreSQLConnectionManager

logger = logging.getLogger(__name__)


class TicketAnalytics:
    """
    Service class handling SLA metrics, business hour calculations, audit logging,
    staff & client analytics aggregations, and holiday schedules.
    """
    
    def __init__(self, conn_manager: Optional[PostgreSQLConnectionManager] = None):
        """
        Initialize the TicketAnalytics service with a PostgreSQL connection manager.
        
        Args:
            conn_manager: Shared database connection pool manager instance.
        """
        self.conn_manager = conn_manager or PostgreSQLConnectionManager()

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
                # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Fix bare except on timestamp parse
                except (ValueError, TypeError):
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

            # Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Fix ticket_metrics schema columns and transaction isolation - start
            if cur:
                cur.execute(query, params)
                inserted = cur.rowcount > 0
                if inserted:
                    try:
                        cur.execute("SAVEPOINT audit_metric_sp")
                        self._update_ticket_metrics_with_cursor(cur, ticket_id, action_type, actor_id, metadata, timestamp)
                        cur.execute("RELEASE SAVEPOINT audit_metric_sp")
                    except Exception as me:
                        logger.error(f"⚠️ Error updating ticket_metrics for {ticket_id}: {me}")
                        cur.execute("ROLLBACK TO SAVEPOINT audit_metric_sp")
            else:
                with self.conn_manager.get_connection() as conn:
                    with conn.cursor() as inner_cur:
                        inner_cur.execute(query, params)
                        inserted = inner_cur.rowcount > 0
                        if inserted:
                            self._update_ticket_metrics_with_cursor(inner_cur, ticket_id, action_type, actor_id, metadata, timestamp)

            return True
        except Exception as e:
            logger.error(f"❌ Failed to log audit event for ticket {ticket_id}: {e}")
            return False

    def get_business_seconds(self, start_dt: datetime, end_dt: datetime, cur) -> int:
        """
        Calculate working seconds between start_dt and end_dt.
        Working hours: 10:00 to 19:00 (9 hrs/day).
        Exclude: Saturdays, Sundays, and dates present in the holidays table.
        """
        if not start_dt or not end_dt or start_dt >= end_dt:
            return 0
            
        cur.execute("SELECT holiday_date FROM holidays")
        holiday_rows = cur.fetchall()
        holidays = {r[0] for r in holiday_rows}
        
        total_seconds = 0
        current = start_dt
        
        while current.date() <= end_dt.date():
            # Check if working day (Mon-Fri and not a holiday)
            if current.weekday() < 5 and current.date() not in holidays:
                day_start = datetime.combine(current.date(), datetime.min.time()).replace(hour=10, minute=0, second=0)
                day_end = datetime.combine(current.date(), datetime.min.time()).replace(hour=19, minute=0, second=0)
                
                # Overlap of [current, end_dt] with [day_start, day_end]
                window_start = max(current, day_start)
                window_end = min(end_dt, day_end)
                
                if window_start < window_end:
                    total_seconds += int((window_end - window_start).total_seconds())
                    
            # Move to the start of the next day
            current = datetime.combine(current.date() + timedelta(days=1), datetime.min.time()).replace(hour=10, minute=0, second=0)
            
        return total_seconds

    def _update_ticket_metrics_with_cursor(self, cur, ticket_id: str, action_type: str, 
                                           actor_id: str, metadata: dict = None, timestamp: datetime = None):
        """
        Incrementally maintain the ticket_metrics table upon audit log events.
        """
        metadata = metadata or {}
        now = timestamp or datetime.now()

        # Fetch or insert ticket_metrics baseline
        cur.execute("SELECT * FROM ticket_metrics WHERE ticket_id = %s FOR UPDATE", (ticket_id,))
        metric = cur.fetchone()

        if not metric:
            cur.execute("""
                SELECT customer_email, assigned_to, source_received_at, created_at
                FROM tickets WHERE ticket_id = %s
            """, (ticket_id,))
            t_row = cur.fetchone()
            if not t_row:
                return  # Ticket doesn't exist yet
            
            c_email, a_agent, s_time, c_time = t_row
            created_at = s_time or c_time or now
            
            cur.execute("""
                INSERT INTO ticket_metrics (ticket_id, customer_email, assigned_agent, created_at)
                VALUES (%s, %s, %s, %s)
            """, (ticket_id, c_email, a_agent, created_at))
            
            cur.execute("SELECT * FROM ticket_metrics WHERE ticket_id = %s FOR UPDATE", (ticket_id,))
            metric = cur.fetchone()

        # Update metrics based on action type matching ticket_metrics schema
        if action_type == ActionType.MESSAGE_RECEIVED:
            cur.execute("""
                UPDATE ticket_metrics 
                SET message_count_customer = message_count_customer + 1,
                    last_updated_at = %s
                WHERE ticket_id = %s
            """, (now, ticket_id))

        elif action_type == ActionType.MESSAGE_SENT:
            # Check if this is the first response
            cur.execute("SELECT first_response_at, created_at FROM ticket_metrics WHERE ticket_id = %s", (ticket_id,))
            row = cur.fetchone()
            if row:
                fr_at, cr_at = row
                if not fr_at and cr_at:
                    duration = self.get_business_seconds(cr_at, now, cur)
                    cur.execute("""
                        UPDATE ticket_metrics 
                        SET first_response_at = %s,
                            first_response_duration = %s,
                            message_count_agent = message_count_agent + 1,
                            last_updated_at = %s
                        WHERE ticket_id = %s
                    """, (now, duration, now, ticket_id))
                else:
                    cur.execute("""
                        UPDATE ticket_metrics 
                        SET message_count_agent = message_count_agent + 1,
                            last_updated_at = %s
                        WHERE ticket_id = %s
                    """, (now, ticket_id))

        elif action_type == ActionType.STATUS_CHANGED:
            new_status = metadata.get('to', '')
            if new_status in ['Closed', 'Resolved']:
                cur.execute("SELECT created_at FROM ticket_metrics WHERE ticket_id = %s", (ticket_id,))
                row = cur.fetchone()
                if row and row[0]:
                    duration = self.get_business_seconds(row[0], now, cur)
                    cur.execute("""
                        UPDATE ticket_metrics 
                        SET resolved_at = %s,
                            total_resolution_duration = %s,
                            last_updated_at = %s
                        WHERE ticket_id = %s
                    """, (now, duration, now, ticket_id))
            elif new_status == 'Reopened':
                cur.execute("""
                    UPDATE ticket_metrics 
                    SET reopen_count = reopen_count + 1,
                        resolved_at = NULL,
                        total_resolution_duration = NULL,
                        last_updated_at = %s
                    WHERE ticket_id = %s
                """, (now, ticket_id))

        elif action_type == ActionType.ASSIGNMENT_CHANGED:
            new_agent = metadata.get('to')
            if new_agent:
                cur.execute("""
                    UPDATE ticket_metrics 
                    SET assigned_agent = %s,
                        last_updated_at = %s
                    WHERE ticket_id = %s
                """, (new_agent, now, ticket_id))
            # Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Fix ticket_metrics schema columns and transaction isolation - end

    def log_ticket_event(self, ticket_id: str, event_type: str, actor: str, details: dict = None, cur = None) -> bool:
        """
        Log an event for a ticket. If a database cursor is provided, it uses it within an existing transaction.
        Otherwise, it acquires a new connection from the pool.
        """
        # Map legacy event_type to ActionType
        action_map = {
            'created': ActionType.TICKET_CREATED,
            'message_received': ActionType.MESSAGE_RECEIVED,
            'response_sent': ActionType.MESSAGE_SENT,
            'internal_note': ActionType.NOTE_ADDED,
            'status_changed': ActionType.STATUS_CHANGED,
            'assigned': ActionType.ASSIGNMENT_CHANGED,
            'ai_draft_generated': ActionType.AI_DRAFT_GENERATED,
            'ai_draft_updated': ActionType.AI_DRAFT_UPDATED,
            'deleted': ActionType.TICKET_DELETED,
            'restored': ActionType.TICKET_RESTORED,
            'devops_linked': ActionType.DEVOPS_ITEM_LINKED,
            'devops_unlinked': ActionType.DEVOPS_ITEM_UNLINKED
        }
        action_type = action_map.get(event_type, event_type.upper())
        actor_type = ActorType.SYSTEM if actor == 'system' else (ActorType.AI if actor == 'AI' else ActorType.USER)
        
        return self.log_audit_event(
            ticket_id=ticket_id,
            action_type=action_type,
            actor_type=actor_type,
            actor_id=actor,
            metadata=details,
            cur=cur
        )

    def log_response_sent(self, ticket_id: str, actor: str, recipient: str = None) -> bool:
        """Log that a response was sent to the customer."""
        return self.log_audit_event(
            ticket_id=ticket_id,
            action_type=ActionType.MESSAGE_SENT,
            actor_type=ActorType.USER,
            actor_id=actor,
            description=f"Response sent to {recipient or 'customer'} by {actor}.",
            metadata={'recipient': recipient}
        )

    def get_staff_metrics(self, staff_username: str, time_range: str = 'all', from_date: str = None, to_date: str = None, status: str = None) -> Dict:
        """Get Assigned, Open, Review, Ignore, and Closed ticket counts for a specific staff member."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    time_filter, params = self._get_time_filter(time_range, from_date, to_date, alias='t')
                    
                    status_filter = ""
                    query_params = [staff_username] + params
                    if status:
                        if status.lower() == 'open':
                            status_filter = " AND (t.status IN ('Open', 'Pending', 'In Progress') OR t.status IS NULL)"
                        elif status.lower() == 'review':
                            status_filter = " AND t.status IN ('Review', 'Pending Review')"
                        elif status.lower() == 'ignore':
                            status_filter = " AND t.status = 'Ignore'"
                        elif status.lower() in ['closed', 'close']:
                            status_filter = " AND t.status IN ('Closed', 'Resolved', 'Completed', 'Deleted')"
                        elif status.lower() == 'reopen':
                            status_filter = " AND (m.reopen_count > 0 OR t.reopened = TRUE)"
                        else:
                            status_filter = " AND t.status = %s"
                            query_params.insert(1, status)
                    
                    assigned_col = "COUNT(t.id)" if status and status.lower() == 'ignore' else "COUNT(t.id) FILTER (WHERE t.status != 'Ignore')"
                    
                    query = f"""
                        SELECT 
                            {assigned_col} as assigned,
                            COUNT(t.id) FILTER (WHERE t.status IN ('Open', 'Pending', 'In Progress') OR t.status IS NULL) as open,
                            COUNT(t.id) FILTER (WHERE t.status IN ('Review', 'Pending Review')) as review,
                            COUNT(t.id) FILTER (WHERE t.status = 'Ignore') as ignore,
                            COUNT(t.id) FILTER (WHERE t.status IN ('Closed', 'Resolved', 'Completed', 'Deleted')) as closed,
                            AVG(CASE WHEN t.status != 'Ignore' THEN m.first_response_duration END) / 60 as avg_frt,
                            AVG(CASE WHEN t.status != 'Ignore' THEN m.total_resolution_duration END) / 3600 as avg_res,
                            COALESCE(SUM(CASE WHEN t.status != 'Ignore' THEN m.reopen_count END), 0) as reopens,
                            AVG(CASE WHEN t.status != 'Ignore' THEN COALESCE(msg_metrics.iterations, 0) END) AS avg_iterations,
                            AVG(CASE WHEN t.status != 'Ignore' THEN EXTRACT(EPOCH FROM (m.resolved_at - t.created_at)) END) / 60 AS avg_ticket_life,
                            AVG(CASE WHEN t.status != 'Ignore' THEN msg_metrics.avg_client_resp_mins END) as avg_client_resp,
                            COUNT(t.id) FILTER (WHERE t.status IN ('Closed', 'Resolved', 'Completed', 'Deleted') AND t.status != 'Ignore' AND m.message_count_agent = 1 AND m.reopen_count = 0) as fcr_count
                        FROM tickets t
                        LEFT JOIN ticket_metrics m ON t.ticket_id = m.ticket_id
                        LEFT JOIN (
                            WITH msg_lag AS (
                                SELECT 
                                    ticket_id,
                                    timestamp,
                                    is_internal,
                                    LAG(is_internal) OVER (PARTITION BY ticket_id ORDER BY timestamp) as prev_internal,
                                    LAG(timestamp) OVER (PARTITION BY ticket_id ORDER BY timestamp) as prev_timestamp
                                FROM ticket_messages
                                WHERE deleted_at IS NULL
                            )
                            SELECT 
                                ticket_id,
                                COUNT(CASE WHEN is_internal = TRUE AND (prev_internal = FALSE OR prev_internal IS NULL) THEN 1 END) as iterations,
                                AVG(CASE WHEN is_internal = FALSE AND prev_internal = TRUE THEN EXTRACT(EPOCH FROM (timestamp - prev_timestamp)) / 60 END) as avg_client_resp_mins
                            FROM msg_lag
                            GROUP BY ticket_id
                        ) msg_metrics ON t.ticket_id = msg_metrics.ticket_id
                        WHERE t.assigned_to = %s {status_filter}
                    """
                    
                    if time_filter:
                        query += f" AND {time_filter}"
                    
                    cur.execute(query, query_params)
                    row = cur.fetchone()
                    
                    return {
                        'username': staff_username,
                        'assigned': row[0] or 0,
                        'open': row[1] or 0,
                        'review': row[2] or 0,
                        'ignore': row[3] or 0,
                        'closed': row[4] or 0,
                        'avg_frt': round(row[5] or 0, 1),
                        'avg_res': round(row[6] or 0, 1),
                        'reopens': int(row[7] or 0),
                        'avg_iterations': round(row[8] or 0, 2),
                        'avg_ticket_life': round(row[9] or 0, 1),
                        'avg_client_resp': round(row[10] or 0, 1),
                        'fcr': int(row[11] or 0)
                    }
        except Exception as e:
            logger.error(f"❌ Error getting staff metrics for {staff_username}: {e}")
            return {
                'username': staff_username, 'assigned': 0, 'open': 0, 'review': 0, 'ignore': 0, 'closed': 0,
                'avg_frt': 0, 'avg_res': 0, 'reopens': 0, 'avg_iterations': 0,
                'avg_ticket_life': 0, 'avg_client_resp': 0, 'fcr': 0
            }

    def get_all_staff_metrics(self, staff_usernames: List[str], time_range: str = 'all', from_date: str = None, to_date: str = None) -> List[Dict]:
        """Get metrics for multiple staff members."""
        metrics = []
        for username in staff_usernames:
            metrics.append(self.get_staff_metrics(username, time_range, from_date, to_date))
        return metrics

    def get_client_stats(self, time_range: str = 'all', from_date: str = None, to_date: str = None, status: str = None, domain_filter: str = None) -> List[Dict]:
        """Get ticket statistics grouped by customer domain."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    time_filter, params = self._get_time_filter(time_range, from_date, to_date, alias='t')
                    
                    status_filter = ""
                    query_params = params.copy()
                    if status:
                        if status.lower() == 'open':
                            status_filter = " AND (t.status IN ('Open', 'Pending', 'In Progress') OR t.status IS NULL)"
                        elif status.lower() == 'review':
                            status_filter = " AND t.status IN ('Review', 'Pending Review')"
                        elif status.lower() == 'ignore':
                            status_filter = " AND t.status = 'Ignore'"
                        elif status.lower() in ['closed', 'close']:
                            status_filter = " AND t.status IN ('Closed', 'Resolved', 'Completed', 'Deleted')"
                        elif status.lower() == 'reopen':
                            status_filter = " AND (m.reopen_count > 0 OR t.reopened = TRUE)"
                        else:
                            status_filter = " AND t.status = %s"
                            query_params.append(status)
                            
                    domain_clause = ""
                    if domain_filter:
                        domain_clause = " AND split_part(t.customer_email, '@', 2) = %s"
                        query_params.append(domain_filter)
                    
                    total_col = "COUNT(t.ticket_id)" if status and status.lower() == 'ignore' else "COUNT(t.ticket_id) FILTER (WHERE t.status != 'Ignore')"
                    
                    query = f"""
                        WITH parsed_domains AS (
                            SELECT t.ticket_id, t.customer_email, split_part(t.customer_email, '@', 2) as raw_domain, t.status, t.last_updated, t.created_at, t.reopened,
                                   m.first_response_duration, m.total_resolution_duration, m.reopen_count, m.message_count_agent, m.resolved_at
                            FROM tickets t
                            LEFT JOIN ticket_metrics m ON t.ticket_id = m.ticket_id
                        ),
                        grouped_domains AS (
                            SELECT pd.*,
                                   COALESCE(
                                       (SELECT cg.name 
                                        FROM client_groups cg 
                                        WHERE string_to_array(cg.domains, ',') @> ARRAY[pd.raw_domain]
                                           OR string_to_array(replace(cg.domains, ' ', ''), ',') @> ARRAY[pd.raw_domain]
                                        LIMIT 1),
                                       pd.raw_domain
                                   ) as domain
                            FROM parsed_domains pd
                        )
                        SELECT 
                            t.domain,
                            {total_col} as total,
                            COUNT(t.ticket_id) FILTER (WHERE t.status IN ('Open', 'Pending', 'In Progress') OR t.status IS NULL) as open,
                            COUNT(t.ticket_id) FILTER (WHERE t.status IN ('Review', 'Pending Review')) as review,
                            COUNT(t.ticket_id) FILTER (WHERE t.status = 'Ignore') as ignore,
                            COUNT(t.ticket_id) FILTER (WHERE t.status IN ('Closed', 'Resolved', 'Completed', 'Deleted')) as closed,
                            MAX(t.last_updated) as last_activity,
                            AVG(CASE WHEN t.status != 'Ignore' THEN t.first_response_duration END) / 60 as avg_frt,
                            AVG(CASE WHEN t.status != 'Ignore' THEN t.total_resolution_duration END) / 3600 as avg_res,
                            AVG(CASE WHEN t.status != 'Ignore' THEN COALESCE(msg_metrics.iterations, 0) END) AS avg_iterations,
                            AVG(CASE WHEN t.status != 'Ignore' THEN EXTRACT(EPOCH FROM (t.resolved_at - t.created_at)) END) / 60 AS avg_ticket_life,
                            AVG(CASE WHEN t.status != 'Ignore' THEN msg_metrics.avg_client_resp_mins END) as avg_client_resp,
                            COALESCE(SUM(CASE WHEN t.status != 'Ignore' THEN t.reopen_count END), 0) as reopens,
                            COUNT(t.ticket_id) FILTER (WHERE t.status IN ('Closed', 'Resolved', 'Completed', 'Deleted') AND t.status != 'Ignore' AND t.message_count_agent = 1 AND t.reopen_count = 0) as fcr_count
                        FROM grouped_domains t
                        LEFT JOIN (
                            WITH msg_lag AS (
                                SELECT 
                                    ticket_id,
                                    timestamp,
                                    is_internal,
                                    LAG(is_internal) OVER (PARTITION BY ticket_id ORDER BY timestamp) as prev_internal,
                                    LAG(timestamp) OVER (PARTITION BY ticket_id ORDER BY timestamp) as prev_timestamp
                                FROM ticket_messages
                                WHERE deleted_at IS NULL
                            )
                            SELECT 
                                ticket_id,
                                COUNT(CASE WHEN is_internal = TRUE AND (prev_internal = FALSE OR prev_internal IS NULL) THEN 1 END) as iterations,
                                AVG(CASE WHEN is_internal = FALSE AND prev_internal = TRUE THEN EXTRACT(EPOCH FROM (timestamp - prev_timestamp)) / 60 END) as avg_client_resp_mins
                            FROM msg_lag
                            GROUP BY ticket_id
                        ) msg_metrics ON t.ticket_id = msg_metrics.ticket_id::text
                        WHERE t.customer_email LIKE '%%@%%' {status_filter.replace('t.status', 't.status').replace('m.reopen_count', 't.reopen_count')} {domain_clause.replace('split_part(t.customer_email, \'@\', 2)', 't.domain')}
                    """
                    
                    if time_filter:
                        query += f" AND {time_filter}"
                    
                    query += " GROUP BY domain ORDER BY total DESC"
                    
                    cur.execute(query, query_params)
                    rows = cur.fetchall()
                    
                    return [{
                        'domain': row[0],
                        'total_tickets': row[1],
                        'open_tickets': row[2],
                        'review_tickets': row[3],
                        'ignore_tickets': row[4],
                        'closed_tickets': row[5],
                        'last_activity': row[6].isoformat() if row[6] else None,
                        'avg_frt': round(row[7] or 0, 1),
                        'avg_res': round(row[8] or 0, 1),
                        'avg_iterations': round(row[9] or 0, 2),
                        'avg_ticket_life': round(row[10] or 0, 1),
                        'avg_client_resp': round(row[11] or 0, 1),
                        'reopens': int(row[12] or 0),
                        'fcr': int(row[13] or 0)
                    } for row in rows]
        except Exception as e:
            logger.error(f"❌ Error getting client stats: {e}")
            return []

    def get_ticket_timeline(self, ticket_id: str) -> List[Dict]:
        """Get all lifecycle events for a ticket from the structured audit log."""
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

    def _get_time_filter(self, time_range: str, from_date: str = None, to_date: str = None, alias: str = 't') -> Tuple[str, list]:
        """Helper to generate SQL time filter and params"""
        now = datetime.now()
        if time_range == 'custom' and from_date and to_date:
            try:
                start = None
                for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
                    try:
                        start = datetime.strptime(from_date, fmt)
                        break
                    except ValueError:
                        continue
                if not start:
                    raise ValueError(f"Could not parse from_date: {from_date}")

                end = None
                for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
                    try:
                        end = datetime.strptime(to_date, fmt)
                        break
                    except ValueError:
                        continue
                if not end:
                    raise ValueError(f"Could not parse to_date: {to_date}")

                start = start.replace(hour=0, minute=0, second=0, microsecond=0)
                end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
                return (
                    f"({alias}.created_at >= %s AND {alias}.created_at <= %s) "
                    f"OR EXISTS (SELECT 1 FROM ticket_audit_logs al WHERE al.ticket_id = {alias}.ticket_id AND al.timestamp >= %s AND al.timestamp <= %s)",
                    [start, end, start, end]
                )
            except Exception as e:
                logger.error(f"Error parsing custom dates {from_date} and {to_date}: {e}")
                return "", []
        elif time_range == 'today':
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return (
                f"({alias}.created_at >= %s) "
                f"OR EXISTS (SELECT 1 FROM ticket_audit_logs al WHERE al.ticket_id = {alias}.ticket_id AND al.timestamp >= %s)",
                [start, start]
            )
        elif time_range == 'week':
            start = now - timedelta(days=now.weekday())
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            return (
                f"({alias}.created_at >= %s) "
                f"OR EXISTS (SELECT 1 FROM ticket_audit_logs al WHERE al.ticket_id = {alias}.ticket_id AND al.timestamp >= %s)",
                [start, start]
            )
        elif time_range == 'month':
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            return (
                f"({alias}.created_at >= %s) "
                f"OR EXISTS (SELECT 1 FROM ticket_audit_logs al WHERE al.ticket_id = {alias}.ticket_id AND al.timestamp >= %s)",
                [start, start]
            )
        elif time_range == 'year':
            start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            return (
                f"({alias}.created_at >= %s) "
                f"OR EXISTS (SELECT 1 FROM ticket_audit_logs al WHERE al.ticket_id = {alias}.ticket_id AND al.timestamp >= %s)",
                [start, start]
            )
        else:
            return "", []

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
        """Get aggregated performance metrics for all agents."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT 
                            m.assigned_agent as agent,
                            COUNT(*) as total_tickets,
                            COUNT(*) FILTER (WHERE m.resolved_at IS NOT NULL) as resolved,
                            AVG(m.first_response_duration) / 60 as avg_frt_minutes,
                            AVG(m.total_resolution_duration) / 3600 as avg_resolution_hours,
                            SUM(m.reopen_count) as total_reopens
                        FROM ticket_metrics m
                        JOIN tickets t ON m.ticket_id = t.ticket_id
                        WHERE m.assigned_agent IS NOT NULL 
                          AND m.assigned_agent != 'Unassigned'
                          AND t.status != 'Ignore'
                        GROUP BY m.assigned_agent
                        ORDER BY total_tickets DESC
                    """)
                    return cur.fetchall()
        except Exception as e:
            logger.error(f"❌ Error fetching agent performance: {e}")
            return []

    def get_weekly_analytics_summary(self) -> Dict:
        """Get high-level summary for the weekly report."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT 
                            COUNT(*) as tickets_created,
                            COUNT(*) FILTER (WHERE m.resolved_at IS NOT NULL) as tickets_resolved,
                            AVG(m.first_response_duration) / 60 as avg_frt_mins,
                            AVG(m.total_resolution_duration) / 3600 as avg_res_hours,
                            (COUNT(*) FILTER (WHERE m.first_response_duration < 14400))::FLOAT / 
                                NULLIF(COUNT(*), 0) * 100 as sla_compliance_pct
                        FROM ticket_metrics m
                        JOIN tickets t ON m.ticket_id = t.ticket_id
                        WHERE m.created_at >= NOW() - INTERVAL '7 days'
                          AND t.status != 'Ignore'
                    """)
                    return dict(cur.fetchone()) if cur.rowcount > 0 else {}
        except Exception as e:
            logger.error(f"❌ Error fetching weekly summary: {e}")
            return {}

    def get_holidays(self) -> List[Dict]:
        """Get all holidays from database sorted by date ascending."""
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT holiday_date, holiday_name FROM holidays ORDER BY holiday_date ASC")
                    rows = cur.fetchall()
                    holidays = []
                    for r in rows:
                        h_date = r[0]
                        h_name = r[1]
                        day_name = datetime.combine(h_date, datetime.min.time()).strftime('%A')
                        holidays.append({
                            'date': h_date.isoformat(),
                            'holiday': h_name,
                            'day': day_name
                        })
                    return holidays
        except Exception as e:
            logger.error(f"❌ Error fetching holidays: {e}")
            return []

    def add_holiday(self, holiday_date_str: str, holiday_name: str) -> Dict:
        """Add or merge a holiday record. Date must be unique; duplicate holiday names ignored."""
        holiday_name = holiday_name.strip()
        if not holiday_name:
            raise ValueError("Holiday name cannot be empty")
        try:
            holiday_date = datetime.strptime(holiday_date_str, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"Invalid date format: {holiday_date_str}. Expected YYYY-MM-DD")
        
        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT holiday_name FROM holidays WHERE holiday_date = %s", (holiday_date,))
                    row = cur.fetchone()
                    if row:
                        existing_name = row[0]
                        existing_names = [n.strip() for n in existing_name.split(",") if n.strip()]
                        
                        normalized_names = [n.lower() for n in existing_names]
                        if holiday_name.lower() in normalized_names:
                            return {'status': 'skipped', 'holiday': existing_name, 'date': holiday_date_str}
                        
                        merged_names = existing_names + [holiday_name]
                        merged_name_str = ", ".join(merged_names)
                        cur.execute("UPDATE holidays SET holiday_name = %s WHERE holiday_date = %s", (merged_name_str, holiday_date))
                        return {'status': 'merged', 'holiday': merged_name_str, 'date': holiday_date_str}
                    else:
                        cur.execute("INSERT INTO holidays (holiday_date, holiday_name) VALUES (%s, %s)", (holiday_date, holiday_name))
                        return {'status': 'added', 'holiday': holiday_name, 'date': holiday_date_str}
        except Exception as e:
            logger.error(f"❌ Error adding holiday: {e}")
            raise

    def update_holiday(self, old_date_str: str, new_date_str: str, holiday_name: str) -> Dict:
        """Update a holiday. If the date is changed and the new date exists, merge them."""
        holiday_name = holiday_name.strip()
        if not holiday_name:
            raise ValueError("Holiday name cannot be empty")
        try:
            old_date = datetime.strptime(old_date_str, "%Y-%m-%d").date()
            new_date = datetime.strptime(new_date_str, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("Invalid date format. Expected YYYY-MM-DD")

        try:
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    if old_date != new_date:
                        cur.execute("SELECT holiday_name FROM holidays WHERE holiday_date = %s", (new_date,))
                        target_row = cur.fetchone()
                        if target_row:
                            existing_name = target_row[0]
                            existing_names = [n.strip() for n in existing_name.split(",") if n.strip()]
                            new_names = [n.strip() for n in holiday_name.split(",") if n.strip()]
                            
                            merged_names = existing_names
                            for name in new_names:
                                if name.lower() not in [m.lower() for m in merged_names]:
                                    merged_names.append(name)
                            
                            merged_name_str = ", ".join(merged_names)
                            cur.execute("UPDATE holidays SET holiday_name = %s WHERE holiday_date = %s", (merged_name_str, new_date))
                            cur.execute("DELETE FROM holidays WHERE holiday_date = %s", (old_date,))
                            return {'status': 'merged', 'holiday': merged_name_str, 'date': new_date_str}
                        else:
                            cur.execute("UPDATE holidays SET holiday_date = %s, holiday_name = %s WHERE holiday_date = %s", (new_date, holiday_name, old_date))
                            return {'status': 'updated', 'holiday': holiday_name, 'date': new_date_str}
                    else:
                        cur.execute("UPDATE holidays SET holiday_name = %s WHERE holiday_date = %s", (holiday_name, old_date))
                        return {'status': 'updated', 'holiday': holiday_name, 'date': new_date_str}
        except Exception as e:
            logger.error(f"❌ Error updating holiday: {e}")
            raise

    def delete_holidays(self, dates: List[str]) -> bool:
        """Delete list of holidays by their date strings."""
        if not dates:
            return True
        try:
            parsed_dates = [datetime.strptime(d, "%Y-%m-%d").date() for d in dates]
            with self.conn_manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM holidays WHERE holiday_date = ANY(%s)", (parsed_dates,))
                    return True
        except Exception as e:
            logger.error(f"❌ Error deleting holidays: {e}")
            return False
# Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Ticket analytics, audit logs, and metrics module - end
