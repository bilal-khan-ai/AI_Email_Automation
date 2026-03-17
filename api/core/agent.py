import os
import logging
import signal
import threading
from datetime import datetime
from bs4 import BeautifulSoup

from config import Config
from api.services.vector_db import VectorDatabase
from api.services.graph_connector import GraphConnector, RetryConfig
from api.models.sql_logger import SQLLogger
from api.services.openai_agent import OpenAIAgent
from api.services.image_processor import ImageProcessor
from api.services.tables_processor import TablesProcessor
from api.services.doc_processor import DocProcessor
from api.services.pii_redactor import PIIRedactor

from api.core.matcher import SubjectMatcher
from api.services.cleanup_manager import CleanupManager

logger = logging.getLogger(__name__)

INTERNAL_DOMAIN = "greenwaresolutions.com"

class SupportAgent:
    """
    Production-ready support agent with modularized components.
    Handles the core orchestration of email ingestion and AI responses.
    """
    
    def __init__(self):
        logger.info("=" * 80)
        logger.info("Initializing Support Agent (Modular Mode)")
        logger.info("=" * 80)
        
        # 1. SQL Logger (PostgreSQL)
        self.sql = SQLLogger(os.path.join("data", "support_tickets.db"))
        if not self.sql.authenticate():
            raise SystemError("❌ SQL Init Failed")
        
        # 2. Graph Connector
        retry_config = RetryConfig(max_retries=3, base_delay=1.0)
        self.graph = GraphConnector(
            Config.AZURE_CLIENT_ID,
            Config.AZURE_CLIENT_SECRET,
            Config.AZURE_TENANT_ID,
            retry_config=retry_config
        )
        if not self.graph.authenticate():
            raise SystemError("❌ Graph Auth Failed")
        
        # 3. Vector Database (Unified pgvector)
        logger.info("📚 Initializing unified pgvector database...")
        self.vector_db = VectorDatabase()
        
        # Documentation & Experience now share the same DB
        self.documentation_db = self.vector_db 
        self.experience_db = self.vector_db
        
        # 4. AI Agent
        self.ai = None
        if Config.OPENAI_API_KEY and Config.AUTO_GENERATE_RESPONSES:
            self.ai = OpenAIAgent(Config.OPENAI_API_KEY)
            self.ai.authenticate()
        
        # 5. Processors
        self.img_processor = ImageProcessor()
        self.tables_processor = TablesProcessor()
        self.doc_processor = DocProcessor(image_processor=self.img_processor)
        
        # 6. Core Logic
        self.subject_matcher = SubjectMatcher(similarity_threshold=0.75)
        self.pii_redactor = PIIRedactor()
        
        # 7. Runtime State
        self.user_email = Config.USER_EMAIL
        self.shutdown_event = threading.Event()
        self._shutdown_requested = False # For backward compatibility in main loops
        
        # 8. Services
        self.cleanup_manager = None
        if Config.ENABLE_TICKET_CLEANUP_DAEMON:
            self.cleanup_manager = CleanupManager(
                self.sql, self.vector_db, Config, self.shutdown_event
            )
            self.cleanup_manager.start()
        
        logger.info("✅ Support Agent initialization complete")

    def shutdown(self):
        """Gracefully shut down all services."""
        logger.info("🛑 Initiating graceful shutdown...")
        self.shutdown_event.set()
        self._shutdown_requested = True
        if self.cleanup_manager:
            # Wait for thread if necessary or just let daemon handles it
            pass
        logger.info("✅ Shutdown complete")

    def _clean_html(self, html: str) -> str:
        if not html: return ""
        return BeautifulSoup(html, "html.parser").get_text(separator="\n", strip=True)

    def _is_internal_email(self, sender: str) -> bool:
        if not sender or "@" not in sender: return False
        domain = sender.strip().split('@')[-1].lower()
        return domain == INTERNAL_DOMAIN.lower()

    def ingest_email(self, email: dict) -> str:
        """Ingest email into system. Logic moved from monolithic main.py."""
        msg_id = email.get('id')
        sender = email.get('sender', '').lower()
        subject = email.get('subject', 'No Subject')
        conv_id = email.get('conversation_id')
        
        if self.sql.message_exists(msg_id):
            return None
        
        is_internal = self._is_internal_email(sender)
        email['body'] = self._clean_html(email.get('body', ''))
        
        # Redact PII
        redacted_email = self.pii_redactor.redact_email_content(email)
        
        # Attachment processing
        attachment_descs = self._process_attachments(msg_id, email.get('attachments', []))
        
        # Ticket Linking
        ticket_id = self._link_ticket(conv_id, subject, sender, is_internal)
        if not ticket_id:
            return None
            
        # Logging & Vectorization
        self.sql.log_message(ticket_id, email, is_internal=is_internal)
        
        # Use pgvector for experience storage
        self.experience_db.add_email(
            email_id=redacted_email['id'],
            subject=redacted_email['subject'],
            body=redacted_email['body'],
            sender=redacted_email['sender'],
            image_descriptions=attachment_descs,
            metadata={'ticket_id': ticket_id, 'is_internal': is_internal}
        )
        
        return ticket_id

    def _process_attachments(self, msg_id, attachments) -> list:
        descs = []
        MAX_BYTES = getattr(Config, "MAX_ATTACHMENT_PROCESSING_BYTES", 10 * 1024 * 1024)
        for att in attachments:
            size = att.get('size', 0)
            if size > MAX_BYTES: continue
            
            try:
                content = self.graph.get_attachment_sync(self.user_email, msg_id, att['id'])
                if not content: continue
                
                name = att.get('name', '').lower()
                if name.endswith(('.png', '.jpg', '.jpeg', '.webp')):
                    desc = self.img_processor.process_image(content)
                    if desc: descs.append(f"[Attachment: {name}] {desc}")
                elif name.endswith(('.xlsx', '.csv', '.xlsx')):
                    res = self.tables_processor.process_bytes(content, filename=name)
                    if res.get("combined_text"): descs.append(res["combined_text"])
                elif name.endswith(('.docx', '.pdf')):
                    res = self.doc_processor.process_bytes(content, filename=name)
                    if res.get("combined_text"): descs.append(res["combined_text"])
            except Exception as e:
                logger.error(f"Attachment error: {e}")
        return descs

    def _link_ticket(self, conv_id, subject, sender, is_internal) -> str:
        existing = self.sql.find_ticket_by_conversation_id(conv_id)
        if existing:
            return existing['ticket_id']
        
        if is_internal:
            logger.warning(f"Orphan internal email: {sender}")
            return None
            
        # Fuzzy match for customer
        active = self.sql.get_active_tickets()
        match = self.subject_matcher.find_matching_ticket(subject, sender, active)
        if match:
            return match['ticket_id']
            
        # New ticket
        ticket_id = f"TKT-{conv_id[:8]}" 
        if self.sql.create_ticket(ticket_id, conv_id, subject, sender):
            return ticket_id
        return None

    def generate_ai_response(self, ticket_id: str):
        """Logic moved from monolithic main.py."""
        if not self.ai: return
        
        messages = self.sql.get_thread_messages(ticket_id)
        if not messages or messages[-1]['is_internal'] == 1:
            return
            
        query = messages[-1]['body_text']
        docs = self.documentation_db.query_knowledge_base(query, n_results=Config.TOP_K_RESULTS, source_type='documentation')
        experiences = self.experience_db.search_similar_tickets(query, top_k=Config.TOP_K_RESULTS)
        
        draft, provenance = self.ai.generate_response(
            messages=messages,
            documentation_context=docs,
            experience_context=experiences,
            customer_email=messages[0].get('sender'),
            subject=messages[0].get('subject')
        )
        
        if draft:
            self.sql.update_ai_draft(ticket_id, draft, provenance)
