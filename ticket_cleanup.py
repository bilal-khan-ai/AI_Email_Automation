import argparse
import logging
import sys
from datetime import datetime, timedelta
from config import Config
from modules.sql_logger import SQLLogger

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - CLEANUP - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def parse_date(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        logger.error("❌ Invalid date format. Use YYYY-MM-DD")
        sys.exit(1)

def run_cleanup(mode, date_arg=None):
    sql_logger = SQLLogger()
    cutoff_date = None
    
    # --- 1. DETERMINE RULES ---
    if mode == 'auto':
        days = getattr(Config, 'DAYS_TO_KEEP_TICKET', 30)
        cutoff_date = datetime.now() - timedelta(days=days)
        # Safe Mode: Only Closed tickets, based on last activity
        criteria_sql = "status = 'Closed' AND last_updated < %s"
        date_param = cutoff_date
        logger.info(f"🔄 AUTO MODE: Cleaning CLOSED tickets inactive since {cutoff_date.date()}")
        
    elif mode == 'standard':
        cutoff_date = parse_date(date_arg)
        # Safe Mode: Only Closed tickets, based on last activity
        criteria_sql = "status = 'Closed' AND last_updated < %s"
        date_param = cutoff_date
        logger.info(f"🛡️  STANDARD MODE: Cleaning CLOSED tickets inactive since {cutoff_date.date()}")

    elif mode == 'hard_clean':
        cutoff_date = parse_date(date_arg)
        # Nuclear Mode: ALL tickets (Open or Closed) based on CREATION date
        # (This cleans out old junk regardless of status)
        criteria_sql = "created_at < %s"
        date_param = cutoff_date
        logger.warning(f"⚠️  HARD CLEAN: Deleting ALL tickets created before {cutoff_date.date()}")
    
    # --- 2. EXECUTE DELETION ---
    try:
        with sql_logger.conn_manager.get_connection() as conn:
            with conn.cursor() as cur:
                
                # Step A: Count targets (for logging)
                cur.execute(f"SELECT COUNT(*) FROM tickets WHERE {criteria_sql}", (date_param,))
                count = cur.fetchone()[0]
                
                if count == 0:
                    logger.info("✅ No tickets found matching criteria.")
                    return

                logger.info(f"🗑️  Found {count} tickets to clean. Starting deletion...")

                # Step B: Explicitly DELETE MESSAGES first
                # We use a subquery to find message IDs belonging to the target tickets
                delete_msgs_query = f"""
                    DELETE FROM ticket_messages 
                    WHERE ticket_id IN (
                        SELECT ticket_id FROM tickets WHERE {criteria_sql}
                    )
                """
                cur.execute(delete_msgs_query, (date_param,))
                msgs_deleted = cur.rowcount
                logger.info(f"   ↳ Deleted {msgs_deleted} associated messages.")

                # Step C: DELETE TICKETS
                delete_tickets_query = f"DELETE FROM tickets WHERE {criteria_sql}"
                cur.execute(delete_tickets_query, (date_param,))
                tickets_deleted = cur.rowcount
                logger.info(f"   ↳ Deleted {tickets_deleted} tickets.")
                
                logger.info("✅ Cleanup operation completed successfully.")

    except Exception as e:
        logger.error(f"❌ Database error: {e}")
    finally:
        sql_logger.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Database Cleanup Utility")
    group = parser.add_mutually_exclusive_group(required=True)
    
    group.add_argument('--hard-clean', metavar='YYYY-MM-DD', help="Delete ALL tickets created before date (ignores status)")
    group.add_argument('--standard', metavar='YYYY-MM-DD', help="Delete CLOSED tickets updated before date")
    group.add_argument('--auto', action='store_true', help="Delete CLOSED tickets older than config days")

    args = parser.parse_args()

    if args.hard_clean:
        run_cleanup('hard_clean', args.hard_clean)
    elif args.standard:
        run_cleanup('standard', args.standard)
    elif args.auto:
        run_cleanup('auto')