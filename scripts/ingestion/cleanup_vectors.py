#!/usr/bin/env python3
"""
Vector DB Cleanup Script

This script should be run periodically (e.g., daily via cron) to:
1. Identify soft-deleted tickets/messages in SQL
2. Mark corresponding vectors as deleted in ChromaDB
3. Permanently remove soft-deleted vectors to free disk space

Usage:
    python cleanup_vectors.py [--dry-run]

Options:
    --dry-run    Show what would be deleted without actually deleting
"""

import sys
import os
import argparse
from datetime import datetime, timedelta

# Add project root directory to path to import data_access and services
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from data_access.vector_db import VectorDatabase
from data_access.sql_logger import SQLLogger
from config import Config
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def cleanup_vectors(dry_run: bool = False, days_back: int = 7):
    """
    Clean up soft-deleted vectors from ChromaDB.
    
    Args:
        dry_run: If True, only report what would be deleted
        days_back: Only process deletions from the last N days
    """
    logger.info("=" * 80)
    logger.info("Vector DB Cleanup Script")
    if dry_run:
        logger.info("MODE: DRY RUN (no actual deletions)")
    logger.info("=" * 80)
    
    try:
        # Initialize SQL
        logger.info("Connecting to PostgreSQL...")
        sql = SQLLogger()
        if not sql.authenticate():
            logger.error("Failed to connect to PostgreSQL")
            return 1
        
        # Initialize Vector DB
        logger.info("Connecting to ChromaDB...")
        vector_db = VectorDatabase(
            Config.CHROMA_DB_PATH,
            Config.COLLECTION_NAME
        )
        
        # Get current stats
        total_count = vector_db.get_count()
        active_count = vector_db.get_active_count()
        deleted_count = vector_db.get_deleted_count()
        
        logger.info(f"📊 Current Vector DB Stats:")
        logger.info(f"   Total vectors: {total_count}")
        logger.info(f"   Active vectors: {active_count}")
        logger.info(f"   Soft-deleted vectors: {deleted_count}")
        
        # Get deleted IDs from SQL (last N days)
        since = datetime.now() - timedelta(days=days_back)
        
        logger.info(f"Fetching deleted tickets/messages since {since.isoformat()}...")
        deleted_ticket_ids = sql.get_deleted_tickets(since=since)
        deleted_message_ids = sql.get_deleted_messages(since=since)
        
        all_deleted_ids = deleted_ticket_ids + deleted_message_ids
        
        logger.info(f"📊 Found in SQL:")
        logger.info(f"   Deleted tickets: {len(deleted_ticket_ids)}")
        logger.info(f"   Deleted messages: {len(deleted_message_ids)}")
        logger.info(f"   Total to soft-delete: {len(all_deleted_ids)}")
        
        # Soft-delete in vector DB
        if all_deleted_ids:
            if dry_run:
                logger.info(f"DRY RUN: Would soft-delete {len(all_deleted_ids)} vectors")
            else:
                logger.info(f"Soft-deleting {len(all_deleted_ids)} vectors...")
                soft_deleted = vector_db.soft_delete(all_deleted_ids)
                logger.info(f"✅ Soft-deleted {soft_deleted} vectors")
        else:
            logger.info("ℹ️  No new deletions to process")
        
        # Hard-delete (cleanup) existing soft-deleted vectors
        if deleted_count > 0:
            if dry_run:
                logger.info(f"DRY RUN: Would permanently remove {deleted_count} soft-deleted vectors")
            else:
                logger.info(f"Permanently removing {deleted_count} soft-deleted vectors...")
                removed = vector_db.cleanup_deleted(batch_size=100)
                logger.info(f"✅ Permanently removed {removed} vectors")
                logger.info(f"💾 Disk space freed: ~{removed * 384 / 1024:.2f} KB (estimated)")
        else:
            logger.info("ℹ️  No soft-deleted vectors to clean up")
        
        # Get final stats
        final_total = vector_db.get_count()
        final_active = vector_db.get_active_count()
        final_deleted = vector_db.get_deleted_count()
        
        logger.info("=" * 80)
        logger.info("📊 Final Vector DB Stats:")
        logger.info(f"   Total vectors: {final_total} (was {total_count})")
        logger.info(f"   Active vectors: {final_active} (was {active_count})")
        logger.info(f"   Soft-deleted vectors: {final_deleted} (was {deleted_count})")
        logger.info("=" * 80)
        logger.info("✅ Cleanup complete")
        logger.info("=" * 80)
        
        # Cleanup connections
        sql.close()
        vector_db.cleanup()
        
        return 0
    
    except Exception as e:
        logger.error(f"❌ Cleanup failed: {e}", exc_info=True)
        return 1


def main():
    parser = argparse.ArgumentParser(
        description="Clean up soft-deleted vectors from ChromaDB"
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be deleted without actually deleting'
    )
    parser.add_argument(
        '--days-back',
        type=int,
        default=7,
        help='Only process deletions from the last N days (default: 7)'
    )
    
    args = parser.parse_args()
    
    exit_code = cleanup_vectors(
        dry_run=args.dry_run,
        days_back=args.days_back
    )
    
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
