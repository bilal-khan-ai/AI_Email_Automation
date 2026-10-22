#!/usr/bin/env python3
"""
cleanup_duplicate_messages.py

Safely audit and clean duplicate messages in `ticket_messages` table.
Default mode is --dry-run (safe, read-only).
To execute deletions, run explicitly with --apply.

Usage:
    python scripts/database/cleanup_duplicate_messages.py           # Dry-run audit only
    python scripts/database/cleanup_duplicate_messages.py --apply   # Apply cleanups
"""

import os
import sys
import argparse
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from data_access.sql_logger import SQLLogger

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cleanup_duplicates")


def find_duplicate_messages(cur):
    """
    Find duplicate messages within the same ticket having:
    1. Identical/near-identical body snippet, same sender, and sent within 15 minutes of each other.
    2. Identical message_id (should not exist due to unique constraint, but checked defensively).
    """
    query = """
        SELECT 
            m1.id AS keep_id,
            m1.message_id AS keep_msg_id,
            m2.id AS dupe_id,
            m2.message_id AS dupe_msg_id,
            m1.ticket_id,
            m1.sender,
            m1.timestamp AS time1,
            m2.timestamp AS time2,
            LEFT(m1.body_text, 60) AS snippet
        FROM ticket_messages m1
        JOIN ticket_messages m2 ON m1.ticket_id = m2.ticket_id 
             AND m1.id < m2.id
             AND (m1.deleted_at IS NULL AND m2.deleted_at IS NULL)
             AND (
                 -- Synthetic vs Real ID match
                 (m1.message_id LIKE 'sent_%' AND m2.message_id NOT LIKE 'sent_%')
                 OR (m1.message_id NOT LIKE 'sent_%' AND m2.message_id LIKE 'sent_%')
                 -- Or exact same sender & body snippet within 15 minutes
                 OR (
                     m1.sender = m2.sender
                     AND MD5(COALESCE(m1.body_text, '')) = MD5(COALESCE(m2.body_text, ''))
                     AND ABS(EXTRACT(EPOCH FROM (m1.timestamp - m2.timestamp))) <= 900
                 )
             )
        ORDER BY m1.ticket_id, m1.id;
    """
    cur.execute(query)
    return cur.fetchall()


def main():
    parser = argparse.ArgumentParser(description="Audit and clean duplicate messages in ticket_messages.")
    parser.add_argument("--apply", action="store_true", help="Execute deletion of identified duplicates. Defaults to dry-run.")
    args = parser.parse_args()

    sql_logger = SQLLogger()

    logger.info("=" * 60)
    logger.info(f"Running duplicate message scan (Mode: {'APPLY' if args.apply else 'DRY-RUN'})...")
    logger.info("=" * 60)

    try:
        with sql_logger.conn_manager.get_connection() as conn:
            with conn.cursor() as cur:
                dupes = find_duplicate_messages(cur)
                logger.info(f"Found {len(dupes)} duplicate pairs in ticket_messages.")

                ids_to_remove = set()
                for row in dupes:
                    keep_id, keep_msg, dupe_id, dupe_msg, tkt, sender, t1, t2, snippet = row
                    
                    # Preference: always keep real Graph API message ID over synthetic sent_ ID
                    if str(keep_msg).startswith('sent_') and not str(dupe_msg).startswith('sent_'):
                        delete_id = keep_id
                        actual_keep = dupe_id
                    else:
                        delete_id = dupe_id
                        actual_keep = keep_id

                    ids_to_remove.add(delete_id)
                    logger.info(
                        f"  [Ticket: {tkt[:20]}...] Keep ID: {actual_keep} | Remove Duplicate ID: {delete_id} | "
                        f"Sender: {sender} | Snippet: {snippet[:40]}"
                    )

                logger.info("-" * 60)
                logger.info(f"Total unique duplicate records eligible for removal: {len(ids_to_remove)}")

                if args.apply:
                    if ids_to_remove:
                        cur.execute(
                            "DELETE FROM ticket_messages WHERE id = ANY(%s);",
                            (list(ids_to_remove),)
                        )
                        conn.commit()
                        logger.info(f"✅ Successfully deleted {len(ids_to_remove)} duplicate messages from database.")
                    else:
                        logger.info("No duplicates to remove.")
                else:
                    logger.info("ℹ️  Dry-run complete. No database changes were made. Pass --apply to execute removal.")

    except Exception as e:
        logger.error(f"❌ Error during duplicate cleanup scan: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
