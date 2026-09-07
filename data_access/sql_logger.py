"""
SQL Logger Module — Unified Database Facade.
Provides backward-compatible interface for TicketRepository, TicketAnalytics, and Connection Pool.
"""

# Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Unified SQLLogger facade over repository and analytics - start
import logging
from typing import Optional
from config import Config
from data_access.db_connection import (
    ActionType,
    ActorType,
    PostgreSQLConnectionManager,
)
from data_access.ticket_repository import TicketRepository
from data_access.ticket_analytics import TicketAnalytics

logger = logging.getLogger(__name__)


class SQLLogger(TicketAnalytics, TicketRepository):
    """
    Unified Facade for PostgreSQL ticket management, audit logging, and SLA analytics.
    
    Inherits all persistence and query methods from TicketRepository and TicketAnalytics,
    guaranteeing 100% backward compatibility with existing routes and background workers.
    """
    
    def __init__(self, db_file: str = "data/support_tickets.db"):
        """
        Initialize the SQLLogger facade with a shared PostgreSQL connection pool.
        
        Args:
            db_file: Retained for backward compatibility with legacy SQLite invocations.
        """
        conn_mgr = PostgreSQLConnectionManager(
            getattr(Config, 'POSTGRES_MIN_CONN', 1),
            getattr(Config, 'POSTGRES_MAX_CONN', 10)
        )
        TicketRepository.__init__(self, conn_manager=conn_mgr)
        TicketAnalytics.__init__(self, conn_manager=conn_mgr)
        logger.info("✅ SQLLogger initialized with PostgreSQL backend")

    def close(self):
        """Close all pooled database connections upon shutdown."""
        self.conn_manager.close_all()


# Export public symbols for callers
__all__ = [
    'ActionType',
    'ActorType',
    'PostgreSQLConnectionManager',
    'TicketRepository',
    'TicketAnalytics',
    'SQLLogger',
]
# Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Unified SQLLogger facade over repository and analytics - end