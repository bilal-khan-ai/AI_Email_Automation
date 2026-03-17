import time
import logging
import os
import sys
from config import Config
from api.core.agent import SupportAgent

# Silence noise
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] %(message)s'
)
logger = logging.getLogger(__name__)

def run_worker():
    """
    Background worker loop.
    Uses the new modular SupportAgent.
    """
    logger.info("🚀 Starting Support Agent Background Worker...")
    
    try:
        agent = SupportAgent()
    except Exception as e:
        logger.error(f"❌ Failed to initialize Support Agent: {e}")
        sys.exit(1)

    # 1. Backlog processing
    if Config.PROCESS_BACKLOG_ON_STARTUP:
        agent.run_backlog(days=Config.BACKLOG_DAYS)

    # 2. Live Polling Loop
    logger.info(f"📡 Entering live polling mode (Interval: {Config.POLLING_INTERVAL}s)")
    
    try:
        while not agent._shutdown_requested:
            try:
                # Fetch latest emails
                emails = agent.graph.fetch_latest_emails(
                    agent.user_email,
                    minutes=Config.POLLING_INTERVAL // 60 + 5,
                    top=50
                )
                
                touched_tickets = set()
                for email in emails:
                    if agent._shutdown_requested: break
                    ticket_id = agent.ingest_email(email)
                    if ticket_id:
                        touched_tickets.add(ticket_id)
                
                # Generate AI responses for touched tickets
                for ticket_id in touched_tickets:
                    if agent._shutdown_requested: break
                    agent.generate_ai_response(ticket_id)
                
                # Sleep
                time.sleep(Config.POLLING_INTERVAL)
                
            except Exception as e:
                logger.error(f"❌ Error in live loop: {e}")
                time.sleep(60) # Wait before retry
                
    except KeyboardInterrupt:
        logger.info("👋 Received keyboard interrupt")
    finally:
        agent.shutdown()

if __name__ == "__main__":
    run_worker()