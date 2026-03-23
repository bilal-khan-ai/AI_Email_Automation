import time
import logging
import os
import signal
import sys
import re
import threading
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from difflib import SequenceMatcher
from config import Config
from modules.vector_db import VectorDatabase
from modules.graph_connector import GraphConnector, RetryConfig
from modules.sql_logger import SQLLogger
from modules.openai_agent import OpenAIAgent
from modules.image_processor import ImageProcessor
from modules.tables_processor import TablesProcessor
from modules.doc_processor import DocProcessor
from modules.pii_redactor import PIIRedactor

# Silence HF/Transformers chatter
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_VERBOSITY"] = "error"
os.environ["TRANSFORMERS_VERBOSITY"] = "error"

# Silence extra progress bars if you want
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"

# Keep YOUR logs, mute noisy libs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
logging.getLogger("transformers").setLevel(logging.WARNING)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)

# Azure SDK spam
logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] %(message)s'
)
logger = logging.getLogger(__name__)

INTERNAL_DOMAIN = "greenwaresolutions.com"


class SubjectMatcher:
    """
    Robust fuzzy subject matching for ticket linking.
    
    Design principles:
    - Deterministic (same inputs → same output)
    - Normalized comparison (case, whitespace, prefixes)
    - Token-based similarity (handles word reordering)
    - Configurable threshold
    """
    
    def __init__(self, similarity_threshold: float = 0.75):
        """
        Initialize subject matcher.
        
        Args:
            similarity_threshold: Minimum similarity score (0.0-1.0) for match
        """
        self.similarity_threshold = similarity_threshold
        
        self.strip_patterns = [
            r'^re:\s*',
            r'^fwd:\s*',
            r'^fw:\s*',
            r'^\[.*?\]\s*',
            r'\s*\[#\d+\]',
        ]
    
    def normalize_subject(self, subject: str) -> str:
        """Normalize subject for comparison"""
        if not subject:
            return ""
        
        normalized = subject.lower().strip()
        
        for pattern in self.strip_patterns:
            normalized = re.sub(pattern, '', normalized, flags=re.IGNORECASE)
        
        normalized = ' '.join(normalized.split())
        normalized = re.sub(r'[!?.,:;]+$', '', normalized)
        
        return normalized.strip()
    
    def calculate_similarity(self, subject1: str, subject2: str) -> float:
        """Calculate similarity score between two subjects"""
        norm1 = self.normalize_subject(subject1)
        norm2 = self.normalize_subject(subject2)
        
        if not norm1 or not norm2:
            return 0.0
        
        if norm1 == norm2:
            return 1.0
        
        matcher = SequenceMatcher(None, norm1, norm2)
        return matcher.ratio()
    
    def find_matching_ticket(self, subject: str, sender: str, 
                            active_tickets: list) -> dict:
        """Find matching ticket using fuzzy subject matching"""
        if not active_tickets:
            return None
        
        customer_tickets = [
            t for t in active_tickets
            if t.get('customer_email', '').lower() == sender.lower()
        ]
        
        if not customer_tickets:
            return None
        
        best_match = None
        best_score = 0.0
        
        for ticket in customer_tickets:
            ticket_subject = ticket.get('subject', '')
            score = self.calculate_similarity(subject, ticket_subject)
            
            if score > best_score:
                best_score = score
                best_match = ticket
        
        if best_score >= self.similarity_threshold:
            logger.info(
                f"🔗 Fuzzy match: '{subject}' → '{best_match.get('subject')}' "
                f"(score: {best_score:.2f})"
            )
            return best_match
        
        return None


class SupportAgent:
    """
    Production-ready support agent with medium-priority improvements.
    
    Key improvements:
    - Enhanced AI prompting with proper context handling
    - RAG provenance tracking
    - Vector DB soft-delete mechanism
    - Structured logging with context
    - Resilient Graph API calls
    - Auto-delete daemon for ticket lifecycle management
    """
    
    def __init__(self):
        logger.info("=" * 80)
        logger.info("Initializing Support Agent (Enhanced Mode)")
        logger.info("=" * 80)
        
        # 1. Init SQL (PostgreSQL)
        self.sql = SQLLogger(os.path.join("data", "support_tickets.db"))
        if not self.sql.authenticate():
            raise SystemError("❌ SQL Init Failed")
        
        # 2. Init Graph with retry config
        retry_config = RetryConfig(
            max_retries=3,
            base_delay=1.0,
            max_delay=30.0,
            exponential_base=2.0,
            jitter=True
        )
        
        self.graph = GraphConnector(
            Config.AZURE_CLIENT_ID,
            Config.AZURE_CLIENT_SECRET,
            Config.AZURE_TENANT_ID,
            retry_config=retry_config
        )
        if not self.graph.authenticate():
            raise SystemError("❌ Graph Auth Failed")
        
        # 3. Init Vector DBs - DUAL CONTEXT
        # 3a. Documentation (BookStack) - authoritative
        logger.info("📚 Initializing documentation vector DB (BookStack)...")
        self.documentation_db = VectorDatabase(
            Config.BOOKSTACK_DB_PATH,
            Config.BOOKSTACK_COLLECTION
        )
        
        # 3b. Experience (Past support emails) - advisory
        logger.info("📧 Initializing experience vector DB (Past Support)...")
        self.experience_db = VectorDatabase(
            Config.CHROMA_DB_PATH,  # "./chroma_db"
            Config.COLLECTION_NAME   # "new_support_emails"
        )
        
        # Keep legacy reference for backward compatibility
        self.vector_db = self.experience_db
        
        # 4. Init AI
        self.ai = None
        if Config.OPENAI_API_KEY and Config.AUTO_GENERATE_RESPONSES:
            self.ai = OpenAIAgent(Config.OPENAI_API_KEY)
            self.ai.authenticate()
        
        # 5. Init Image Processor
        self.img_processor = ImageProcessor()
        

        # 5b. Init Table/Document Processors (local preprocessing)
        self.tables_processor = TablesProcessor()
        self.doc_processor = DocProcessor(image_processor=self.img_processor)
        # 6. Init Subject Matcher
        self.subject_matcher = SubjectMatcher(similarity_threshold=0.75)

        # PII Redaction (CRITICAL SECURITY)
        self.pii_redactor = PIIRedactor()
        logger.info("🔐 PII Redactor initialized")

        
        # 7. Runtime state
        self.user_email = Config.USER_EMAIL
        self._shutdown_requested = False
        
        # 8. Setup signal handlers
        self._setup_signal_handlers()
        
        # 9. Start cleanup daemon if enabled
        self.cleanup_daemon_thread = None
        if Config.ENABLE_TICKET_CLEANUP_DAEMON:
            self._start_cleanup_daemon()
        
        logger.info("✅ Support Agent initialization complete")
    
    def _setup_signal_handlers(self):
        """Setup graceful shutdown on SIGINT/SIGTERM"""
        def signal_handler(signum, frame):
            logger.info(f"🛑 Received signal {signum}, initiating shutdown...")
            self._shutdown_requested = True
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    def _start_cleanup_daemon(self):
        """Start the background cleanup daemon thread"""
        logger.info("🧹 Starting cleanup daemon thread...")
        
        self.cleanup_daemon_thread = threading.Thread(
            target=self._cleanup_daemon_loop,
            daemon=True,
            name="TicketCleanupDaemon"
        )
        self.cleanup_daemon_thread.start()
        logger.info("✅ Cleanup daemon started")
    
    def _cleanup_daemon_loop(self):
        """
        Main loop for the cleanup daemon.
        
        Runs every CLEANUP_DAEMON_INTERVAL_SECONDS and executes:
        1. Soft-delete pass
        2. Hard-delete pass
        """
        logger.info(
            f"🔄 Cleanup daemon running | "
            f"Interval: {Config.CLEANUP_DAEMON_INTERVAL_SECONDS}s | "
            f"Soft-delete after: {Config.SOFT_DELETE_CLOSED_AFTER_DAYS}d | "
            f"Hard-delete after: {Config.HARD_DELETE_AFTER_DAYS}d"
        )
        
        while not self._shutdown_requested:
            try:
                # Sleep first (don't run immediately on startup)
                for _ in range(Config.CLEANUP_DAEMON_INTERVAL_SECONDS):
                    if self._shutdown_requested:
                        break
                    time.sleep(1)
                
                if self._shutdown_requested:
                    break
                
                logger.info("=" * 60)
                logger.info("🔄 Running cleanup daemon pass...")
                logger.info("=" * 60)
                
                # Execute both passes
                soft_count = self._soft_delete_pass()
                hard_count = self._hard_delete_pass()
                
                logger.info(
                    f"✅ Cleanup pass complete | "
                    f"Soft-deleted: {soft_count} | Hard-deleted: {hard_count}"
                )
                
            except Exception as e:
                logger.error(f"❌ Cleanup daemon error: {e}")
                # Sleep a bit before retry
                if not self._shutdown_requested:
                    time.sleep(60)
        
        logger.info("🛑 Cleanup daemon stopped")
    
    def _soft_delete_pass(self) -> int:
        """
        Soft-delete eligible closed tickets.
        
        Returns:
            int: Number of tickets soft-deleted
        """
        try:
            cutoff_dt = datetime.now() - timedelta(days=Config.SOFT_DELETE_CLOSED_AFTER_DAYS)
            batch_size = Config.CLEANUP_BATCH_SIZE
            
            # Get candidates
            candidates = self.sql.get_soft_delete_candidates(cutoff_dt, limit=batch_size)
            
            if not candidates:
                logger.debug("ℹ️  No tickets eligible for soft-deletion")
                return 0
            
            deleted_count = 0
            
            for ticket_id in candidates:
                if self._shutdown_requested:
                    break
                
                try:
                    # Get message IDs for vector DB
                    msg_ids = self.sql.get_message_ids_for_ticket(ticket_id)
                    
                    # Soft-delete in SQL
                    if self.sql.soft_delete_ticket(ticket_id):
                        # Soft-delete in vector DB
                        if msg_ids:
                            self.experience_db.soft_delete(msg_ids)
                        
                        deleted_count += 1
                        logger.info(
                            f"🗑️  Soft-deleted ticket {ticket_id} | "
                            f"Messages: {len(msg_ids)}"
                        )
                    
                except Exception as e:
                    logger.error(f"❌ Failed to soft-delete ticket {ticket_id}: {e}")
            
            logger.info(f"📊 Soft-delete pass complete: {deleted_count} tickets processed")
            return deleted_count
            
        except Exception as e:
            logger.error(f"❌ Soft-delete pass error: {e}")
            return 0
    
    def _hard_delete_pass(self) -> int:
        """
        Hard-delete old soft-deleted tickets.
        
        Returns:
            int: Number of tickets hard-deleted
        """
        try:
            cutoff_dt = datetime.now() - timedelta(days=Config.HARD_DELETE_AFTER_DAYS)
            batch_size = Config.CLEANUP_BATCH_SIZE
            
            # Get candidates
            candidates = self.sql.get_hard_delete_candidates(cutoff_dt, limit=batch_size)
            
            if not candidates:
                logger.debug("ℹ️  No tickets eligible for hard-deletion")
                return 0
            
            deleted_count = 0
            
            # First, delete from vector DB
            for ticket_id in candidates:
                if self._shutdown_requested:
                    break
                
                try:
                    msg_ids = self.sql.get_message_ids_for_ticket(ticket_id)
                    
                    if msg_ids:
                        self.experience_db.hard_delete(msg_ids)
                    
                except Exception as e:
                    logger.error(f"❌ Failed to delete vector docs for ticket {ticket_id}: {e}")
            
            # Then, delete from SQL (cascades to messages)
            if candidates and not self._shutdown_requested:
                deleted_count = self.sql.hard_delete_tickets(candidates)
            
            logger.info(f"📊 Hard-delete pass complete: {deleted_count} tickets processed")
            return deleted_count
            
        except Exception as e:
            logger.error(f"❌ Hard-delete pass error: {e}")
            return 0
    
    def _clean_html(self, html: str) -> str:
        """Clean HTML to plain text"""
        if not html:
            return ""
        return BeautifulSoup(html, "html.parser").get_text(separator="\n", strip=True)
    
    def _extract_email_domain(self, email: str) -> str:
        """
        Extracts the domain from an email address.
        Example: 'someone@eXample.com' -> 'example.com'
        """
        if not email or "@" not in email:
            return ""
        domain = email.strip().split('@')[-1]
        
        return domain.lower()
    
    def _is_internal_email(self, sender: str) -> bool:
        """Check if email is from greenwaresolutions.com"""
        return self._extract_email_domain(sender) == INTERNAL_DOMAIN.lower()
    
    def ingest_email(self, email: dict) -> str:
        """
        Ingest email into support system with enhanced logging.
        
        Flow:
        1. Check if message already processed (idempotent)
        2. Determine if customer or internal message
        3. Find or create ticket
        4. Log message to ticket
        5. Add to vector DB
        
        Args:
            email: Email dict from Graph API
            
        Returns:
            ticket_id if processed, None if skipped
        """
        msg_id = email.get('id')
        sender = email.get('sender', '').lower()
        subject = email.get('subject', 'No Subject')
        conv_id = email.get('conversation_id')
        
        # Idempotency check
        if self.sql.message_exists(msg_id):
            logger.debug(f"ℹ️  Message {msg_id[:20]}... already processed (skipping)")
            return None
        
        # Determine if internal
        is_internal = self._is_internal_email(sender)
        
        # Clean body
        email['body'] = self._clean_html(email.get('body', ''))

        # SECURITY: Redact PII before RAG ingestion
        redacted_email = self.pii_redactor.redact_email_content(email)

        if redacted_email.get('pii_redacted'):
            logger.warning(
                f"🔐 PII redacted | Msg: {msg_id[:20]}... | "
                f"Entities: {redacted_email.get('pii_entities_found', 0)}"
            )
        # Process attachments (images + docx/pdf + xlsx/csv)
        # NOTE: we store these as "attachment descriptions" in Vector DB so RAG can use them later.
        attachment_descs = []
        MAX_BYTES = getattr(Config, "MAX_ATTACHMENT_PROCESSING_BYTES", 10 * 1024 * 1024)

        for att in email.get('attachments', []):
            name = att.get('name') or 'attachment'
            ct = (att.get('content_type') or '').lower()
            size = att.get('size') or 0

            # Skip huge files (safety). You can override via Config.MAX_ATTACHMENT_PROCESSING_BYTES
            if size and size > MAX_BYTES:
                logger.warning(
                    f"⏭️  Skipping large attachment ({size} bytes) | "
                    f"Msg: {msg_id[:20]}... | Att: {name}"
                )
                continue

            lower_name = name.lower()

            is_img = ct in ('image/jpeg', 'image/png', 'image/jpg', 'image/webp') or lower_name.endswith(('.png', '.jpg', '.jpeg', '.webp'))
            is_table = (
                lower_name.endswith(('.xlsx', '.xlsm', '.csv', '.tsv')) or
                ct in (
                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    'application/vnd.ms-excel',
                    'text/csv',
                    'text/tab-separated-values'
                )
            )
            is_doc = (
                lower_name.endswith(('.docx', '.pdf')) or
                ct in (
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    'application/pdf'
                )
            )

            if not (is_img or is_table or is_doc):
                continue

            try:
                att_bytes = self.graph.get_attachment_sync(
                    self.user_email,
                    msg_id,
                    att['id']
                )

                if not att_bytes:
                    continue

                if is_img:
                    desc = self.img_processor.process_image(att_bytes)
                    if desc:
                        attachment_descs.append(f"[Attachment: {name}] {desc}")
                elif is_table:
                    res = self.tables_processor.process_bytes(att_bytes, filename=name)
                    txt = (res or {}).get("combined_text", "")
                    if txt:
                        attachment_descs.append(txt)
                elif is_doc:
                    res = self.doc_processor.process_bytes(att_bytes, filename=name)
                    txt = (res or {}).get("combined_text", "")
                    if txt:
                        attachment_descs.append(txt)

            except Exception as e:
                logger.error(
                    f"❌ Failed to process attachment | "
                    f"Msg: {msg_id[:20]}... | Att: {name}: {e}"
                )

        # Find or create ticket

        ticket_id = None
        
        if not is_internal:
            # CUSTOMER MESSAGE - create or link ticket
            
            # Try conversation ID match first
            existing = self.sql.find_ticket_by_conversation_id(conv_id)
            
            if existing:
                ticket_id = existing['ticket_id']
                logger.info(
                    f"🔗 Linked to existing ticket {ticket_id} | "
                    f"ConvID match | Sender: {sender}"
                )
            else:
                # Try fuzzy subject match
                active_tickets = self.sql.get_active_tickets()
                fuzzy_match = self.subject_matcher.find_matching_ticket(
                    subject, sender, active_tickets
                )
                
                if fuzzy_match:
                    ticket_id = fuzzy_match['ticket_id']
                    logger.info(
                        f"🔗 Linked to existing ticket {ticket_id} | "
                        f"Fuzzy match | Sender: {sender}"
                    )
                else:
                    # Create new ticket
                    ticket_id = f"TKT-{msg_id}"
                    
                    success = self.sql.create_ticket(
                        ticket_id, conv_id, subject, sender
                    )
                    
                    if success:
                        logger.info(
                            f"✨ Created new ticket {ticket_id} | "
                            f"Customer: {sender} | Subject: {subject[:50]}"
                        )
                    else:
                        logger.error(
                            f"❌ Failed to create ticket | "
                            f"Msg: {msg_id[:20]}... | Sender: {sender}"
                        )
                        return None
            
            # Log message
            self.sql.log_message(ticket_id, email, is_internal=False)
            
            # Add to vector DB
            self.experience_db.add_email(
                email_id=redacted_email['id'],
                subject=redacted_email['subject'],
                body=redacted_email['body'],
                sender=redacted_email['sender'],
                image_descriptions=attachment_descs,
                metadata={
                    'ticket_id': ticket_id,
                    'pii_redacted': redacted_email.get('pii_redacted', False),
                    'pii_entities_count': redacted_email.get('pii_entities_found', 0)
                }
            )

        
        else:
            # INTERNAL MESSAGE - must have parent ticket
            
            existing = self.sql.find_ticket_by_conversation_id(conv_id)
            
            if existing:
                ticket_id = existing['ticket_id']
                
                logger.info(
                    f"📨 Internal reply to ticket {ticket_id} | "
                    f"Sender: {sender}"
                )
                
                # Log message
                self.sql.log_message(ticket_id, email, is_internal=True)
                
                # Add to vector DB (for RAG context)
                self.experience_db.add_email(
                    email_id=redacted_email['id'],
                    subject=redacted_email['subject'],
                    body=redacted_email['body'],
                    sender=redacted_email['sender'],
                    image_descriptions=attachment_descs,
                    metadata={
                        'ticket_id': ticket_id,
                        'pii_redacted': redacted_email.get('pii_redacted', False),
                        'pii_entities_count': redacted_email.get('pii_entities_found', 0)
                    }
                )

            else:
                logger.warning(
                    f"⚠️  Orphan internal email skipped (no parent ticket) | "
                    f"Msg: {msg_id[:20]}... | Sender: {sender}"
                )
                return None
        
        return ticket_id
    
    def generate_ai_response(self, ticket_id: str):
        """
        Generate AI response with dual-context RAG (documentation + experience).
        
        ENHANCEMENT:
        - Searches both documentation and experience databases
        - Passes contexts separately to AI agent
        - Tracks provenance from both sources
        """
        if not self.ai:
            return
        
        # 1. Fetch thread
        messages = self.sql.get_thread_messages(ticket_id)
        if not messages:
            logger.warning(f"⚠️  No messages found for ticket {ticket_id}")
            return
        
        # 2. Check last speaker
        last_msg = messages[-1]
        if last_msg['is_internal'] == 1:
            logger.info(
                f"🛑 Ticket {ticket_id}: Support replied last. No AI needed."
            )
            return
        
        logger.info(f"🧠 Generating AI Draft for ticket {ticket_id}...")
        
        # 3. DUAL RAG SEARCH
        query = last_msg['body_text']
        
        # 3a. Search documentation (authoritative)
        docs = self.documentation_db.search_similar(
            query, 
            top_k=Config.TOP_K_RESULTS
        )
        logger.info(f"📚 Documentation search: {len(docs)} chunks found")
        
        # 3b. Search experience (advisory)
        experiences = self.experience_db.search_similar(
            query, 
            top_k=Config.TOP_K_RESULTS
        )
        logger.info(f"📧 Experience search: {len(experiences)} past cases found")
        
        # 4. Generate response with SEPARATE contexts
        draft, provenance = self.ai.generate_response(
            messages=messages,
            documentation_context=docs,      # NEW: Pass docs separately
            experience_context=experiences,   # NEW: Pass experience separately
            customer_email=messages[0].get('sender'),
            subject=messages[0].get('subject')
        )
        
        # 5. Save draft with combined provenance
        if draft:
            self.sql.update_ai_draft(ticket_id, draft, provenance)
            
            if provenance:
                # Break down provenance by source
                doc_prov = [p for p in provenance if p.get('source') == 'documentation']
                exp_prov = [p for p in provenance if p.get('source') == 'experience']
                
                logger.info(
                    f"✅ Draft saved for {ticket_id} | "
                    f"Docs used: {len(doc_prov)} | "
                    f"Experience used: {len(exp_prov)}"
                )
                
                # Log top similarity scores
                if doc_prov:
                    logger.info(
                        f"   📚 Top doc similarity: {doc_prov[0].get('similarity_score', 0):.2%}"
                    )
                if exp_prov:
                    logger.info(
                        f"   📧 Top experience similarity: {exp_prov[0].get('similarity_score', 0):.2%}"
                    )
            else:
                logger.info(f"✅ Draft saved for {ticket_id} (no RAG context)")
        else:
            logger.error(f"❌ Failed to generate draft for {ticket_id}")
    
    def run_backlog(self, days: int = 7):
        """
        Process backlog of emails with enhanced error handling.
        
        Args:
            days: Number of days to look back
        """
        logger.info("=" * 80)
        logger.info(f"Processing backlog ({days} days)")
        logger.info("=" * 80)
        
        # Fetch emails
        mins = days * 24 * 60
        emails = self.graph.fetch_latest_emails(
            self.user_email,
            minutes=mins,
            top=500
        )
        
        # Sort oldest -> newest
        emails.sort(key=lambda x: x['received'])
        
        logger.info(f"📊 Found {len(emails)} emails in backlog")
        
        # Process each email
        touched = set()
        success_count = 0
        error_count = 0
        
        for i, email in enumerate(emails):
            if self._shutdown_requested:
                logger.info("🛑 Shutdown requested, stopping backlog processing")
                break
            
            try:
                ticket_id = self.ingest_email(email)
                if ticket_id:
                    touched.add(ticket_id)
                    success_count += 1
                
                # Progress logging
                if (i + 1) % 10 == 0:
                    logger.info(
                        f"📊 Progress: {i + 1}/{len(emails)} emails | "
                        f"Success: {success_count} | Errors: {error_count}"
                    )
            
            except Exception as e:
                error_count += 1
                logger.error(
                    f"❌ Error processing email | "
                    f"ID: {email.get('id', 'unknown')[:20]}... | "
                    f"Sender: {email.get('sender', 'unknown')}: {e}"
                )
        
        # Generate AI responses
        logger.info(f"🧠 Generating AI for {len(touched)} active tickets...")
        
        ai_success = 0
        ai_errors = 0
        
        for ticket_id in touched:
            if self._shutdown_requested:
                break
            try:
                self.generate_ai_response(ticket_id)
                ai_success += 1
            except Exception as e:
                ai_errors += 1
                logger.error(f"❌ Error generating AI for {ticket_id}: {e}")
        
        logger.info("=" * 80)
        logger.info("Backlog processing complete")
        logger.info(f"📊 Email ingestion: {success_count} success, {error_count} errors")
        logger.info(f"📊 AI generation: {ai_success} success, {ai_errors} errors")
        logger.info("=" * 80)
    
    def run_live(self):
        """
        Live polling loop with enhanced error handling.
        """
        logger.info("=" * 80)
        logger.info("Starting live polling")
        logger.info("=" * 80)
        
        while not self._shutdown_requested:
            try:
                # Fetch recent emails
                emails = self.graph.fetch_latest_emails(
                    self.user_email,
                    minutes=10
                )
                
                # Sort oldest -> newest
                emails.sort(key=lambda x: x['received'])
                
                if emails:
                    logger.info(f"📧 Processing {len(emails)} new emails...")
                
                # Process emails
                touched = set()
                for email in emails:
                    if self._shutdown_requested:
                        break
                    
                    try:
                        ticket_id = self.ingest_email(email)
                        if ticket_id:
                            touched.add(ticket_id)
                    except Exception as e:
                        logger.error(
                            f"❌ Error processing email | "
                            f"ID: {email.get('id', 'unknown')[:20]}...: {e}"
                        )
                
                # Generate AI responses
                for ticket_id in touched:
                    if self._shutdown_requested:
                        break
                    try:
                        self.generate_ai_response(ticket_id)
                    except Exception as e:
                        logger.error(f"❌ Error generating AI for {ticket_id}: {e}")
                
                # Sleep until next poll
                if not self._shutdown_requested:
                    time.sleep(Config.POLLING_INTERVAL)
            
            except Exception as e:
                logger.error(f"❌ Live loop error: {e}")
                if not self._shutdown_requested:
                    time.sleep(10)  # Brief pause before retry
        
        logger.info("🛑 Live polling stopped")
    
    def cleanup(self):
        """Clean up all resources on shutdown"""
        logger.info("=" * 80)
        logger.info("Cleaning up resources")
        logger.info("=" * 80)
        
        # Signal daemon to stop
        self._shutdown_requested = True
        
        # Wait for daemon thread to finish
        if self.cleanup_daemon_thread and self.cleanup_daemon_thread.is_alive():
            logger.info("⏳ Waiting for cleanup daemon to stop...")
            self.cleanup_daemon_thread.join(timeout=10)
        
        try:
            if self.graph:
                logger.info("🧹 Shutting down Graph connector...")
                self.graph.shutdown()
        except Exception as e:
            logger.error(f"❌ Error shutting down Graph: {e}")
        
        try:
            if self.img_processor:
                logger.info("🧹 Cleaning up image processor...")
                self.img_processor.cleanup()
        except Exception as e:
            logger.error(f"❌ Error cleaning up image processor: {e}")
        
        try:
            if hasattr(self, 'documentation_db') and self.documentation_db:
                logger.info("🧹 Cleaning up documentation vector DB...")
                self.documentation_db.cleanup()
        except Exception as e:
            logger.error(f"❌ Error cleaning up documentation DB: {e}")
        
        try:
            if hasattr(self, 'experience_db') and self.experience_db:
                logger.info("🧹 Cleaning up experience vector DB...")
                self.experience_db.cleanup()
        except Exception as e:
            logger.error(f"❌ Error cleaning up experience DB: {e}")
        
        # Legacy cleanup for backward compatibility
        try:
            if hasattr(self, 'vector_db') and self.vector_db and self.vector_db != self.experience_db:
                logger.info("🧹 Cleaning up legacy vector DB...")
                self.vector_db.cleanup()
        except Exception as e:
            logger.error(f"❌ Error cleaning up vector DB: {e}")
        
        try:
            if self.sql:
                logger.info("🧹 Closing SQL connections...")
                self.sql.close()
        except Exception as e:
            logger.error(f"❌ Error closing SQL: {e}")
        
        logger.info("=" * 80)
        logger.info("✅ Cleanup complete")
        logger.info("=" * 80)


if __name__ == "__main__":
    agent = None
    try:
        # Initialize agent
        agent = SupportAgent()
        
        # Process backlog
        agent.run_backlog(days=getattr(Config, 'PROCESSING_DAYS_BACK', 0))
        
        # Start live polling
        agent.run_live()
    
    except KeyboardInterrupt:
        logger.info("🛑 Interrupted by user")
    
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        sys.exit(1)
    
    finally:
        # Always clean up
        if agent:
            agent.cleanup()
        
        logger.info("👋 Application stopped")