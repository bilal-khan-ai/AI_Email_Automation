from datetime import datetime, timedelta
import logging
import time
import threading

logger = logging.getLogger(__name__)

class CleanupManager:
    """
    Handles background cleanup tasks for the support system.
    Extracted from SupportAgent for modularity.
    """
    
    def __init__(self, sql_logger, vector_db, config, shutdown_event):
        self.sql = sql_logger
        self.vector_db = vector_db
        self.config = config
        self.shutdown_event = shutdown_event
        self.thread = None

    def start(self):
        """Start the background cleanup daemon thread"""
        logger.info("🧹 Starting cleanup daemon thread...")
        self.thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="TicketCleanupDaemon"
        )
        self.thread.start()
        logger.info("✅ Cleanup daemon started")

    def _run_loop(self):
        """Main loop for the cleanup daemon."""
        logger.info(
            f"🔄 Cleanup daemon running | "
            f"Interval: {self.config.CLEANUP_DAEMON_INTERVAL_SECONDS}s"
        )
        
        while not self.shutdown_event.is_set():
            try:
                # Sleep in small increments to respond quickly to shutdown
                for _ in range(self.config.CLEANUP_DAEMON_INTERVAL_SECONDS):
                    if self.shutdown_event.is_set():
                        break
                    time.sleep(1)
                
                if self.shutdown_event.is_set():
                    break
                
                logger.info("=" * 60)
                logger.info("🔄 Running cleanup daemon pass...")
                logger.info("=" * 60)
                
                soft_count = self._soft_delete_pass()
                hard_count = self._hard_delete_pass()
                
                logger.info(
                    f"✅ Cleanup pass complete | "
                    f"Soft-deleted: {soft_count} | Hard-deleted: {hard_count}"
                )
                
            except Exception as e:
                logger.error(f"❌ Cleanup daemon error: {e}")
                if not self.shutdown_event.is_set():
                    time.sleep(60)
        
        logger.info("🛑 Cleanup daemon stopped")

    def _soft_delete_pass(self) -> int:
        """Soft-delete eligible closed tickets."""
        try:
            cutoff_dt = datetime.now() - timedelta(days=self.config.SOFT_DELETE_CLOSED_AFTER_DAYS)
            batch_size = self.config.CLEANUP_BATCH_SIZE
            candidates = self.sql.get_soft_delete_candidates(cutoff_dt, limit=batch_size)
            
            if not candidates:
                return 0
            
            deleted_count = 0
            for ticket_id in candidates:
                if self.shutdown_event.is_set():
                    break
                
                msg_ids = self.sql.get_message_ids_for_ticket(ticket_id)
                if self.sql.soft_delete_ticket(ticket_id):
                    # No longer need vector_db.soft_delete because pgvector is unified?
                    # Actually, if we use a separate knowledge_base for deleted docs, we might.
                    # But for pgvector, soft-deletion is usually just a flag in SQL.
                    # We'll keep the vector_db call for consistency if it still has logic.
                    if msg_ids and hasattr(self.vector_db, 'soft_delete'):
                         self.vector_db.soft_delete(msg_ids)
                    
                    deleted_count += 1
            return deleted_count
        except Exception as e:
            logger.error(f"❌ Soft-delete pass error: {e}")
            return 0

    def _hard_delete_pass(self) -> int:
        """Hard-delete old soft-deleted tickets."""
        try:
            cutoff_dt = datetime.now() - timedelta(days=self.config.HARD_DELETE_AFTER_DAYS)
            candidates = self.sql.get_hard_delete_candidates(cutoff_dt, limit=self.config.CLEANUP_BATCH_SIZE)
            
            if not candidates:
                return 0
            
            # Delete from vector DB first if applicable
            for ticket_id in candidates:
                if self.shutdown_event.is_set():
                    break
                msg_ids = self.sql.get_message_ids_for_ticket(ticket_id)
                if msg_ids and hasattr(self.vector_db, 'hard_delete'):
                    self.vector_db.hard_delete(msg_ids)
            
            # Then delete from SQL
            return self.sql.hard_delete_tickets(candidates)
        except Exception as e:
            logger.error(f"❌ Hard-delete pass error: {e}")
            return 0
