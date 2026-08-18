import time
import logging
import os
import signal
import sys
import re
import threading
import gc  # Bilal Khan (12/08/2026) - P1 Fix #7: explicit GC for post-batch memory reclaim
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from difflib import SequenceMatcher
from config import Config
# Bilal Khan (05/08/2026) - Model unload: heavy ML imports moved to conditional (only loaded when ENABLE_RAG=True)
# VectorDatabase, ImageProcessor, TablesProcessor, DocProcessor, PIIRedactor
# are imported inside SupportAgent.__init__ under the ENABLE_RAG guard.
# Importing them at module level forces Python to load their C-extensions (spaCy,
# chromadb, presidio) even when they are never instantiated.
from modules.graph_connector import GraphConnector, RetryConfig
from modules.sql_logger import SQLLogger
from modules.openai_agent import OpenAIAgent

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
    
    def _extract_domain(self, email: str) -> str:
        """Extract domain from email address"""
        if not email or "@" not in email:
            return ""
        return email.lower().strip().split('@')[-1]

    def find_matching_ticket(self, subject: str, sender: str, 
                            active_tickets: list) -> dict:
        """
        Find matching ticket using fuzzy subject matching with domain awareness.
        
        Logic:
        1. Identifies the sender's domain.
        2. If domain is a common public provider (Gmail, etc.), strictly matches by email.
        3. Otherwise, allows matching against any active ticket from the same domain.
        4. Prioritizes exact email matches over domain matches.
        """
        if not active_tickets:
            return None
        
        sender_email = sender.lower()
        sender_domain = self._extract_domain(sender_email)
        
        # Generic public domains where grouping by domain is risky/incorrect
        public_domains = {
            'gmail.com', 'outlook.com', 'hotmail.com', 'yahoo.com', 
            'icloud.com', 'me.com', 'live.com', 'msn.com', 'aol.com',
            'rediffmail.com', 'protonmail.com', 'zoho.com', 'ymail.com'
        }
        
        is_public = sender_domain in public_domains
        
        # Filter tickets by sender identity or domain
        candidates = []
        for t in active_tickets:
            ticket_email = t.get('customer_email', '').lower()
            ticket_domain = self._extract_domain(ticket_email)
            
            if ticket_email == sender_email:
                # Same person: Highest priority
                candidates.append((t, 1.0))
            elif not is_public and ticket_domain == sender_domain and ticket_domain != "":
                # Same organization: Allowed, but slightly lower weight than exact person
                candidates.append((t, 0.95))
        
        if not candidates:
            return None
        
        best_match = None
        best_score = 0.0
        
        for ticket, weight in candidates:
            ticket_subject = ticket.get('subject', '')
            # Calculate similarity and apply weight
            similarity = self.calculate_similarity(subject, ticket_subject)
            score = similarity * weight
            
            if score > best_score:
                best_score = score
                best_match = ticket
        
        # Use raw similarity for final threshold check to maintain consistency
        raw_similarity = self.calculate_similarity(subject, best_match.get('subject', ''))
        
        if raw_similarity >= self.similarity_threshold:
            match_type = "user" if best_match.get('customer_email', '').lower() == sender_email else "domain"
            logger.info(
                f"🔗 Fuzzy match ({match_type}): '{subject}' → '{best_match.get('subject')}' "
                f"(score: {raw_similarity:.2f})"
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
        
        # 3. Vector DBs, Image/Doc processors, and PII redactor
        # Bilal Khan (05/08/2026) - Model unload: skip all heavy ML subsystems when RAG disabled - start
        # When ENABLE_RAG=False (current production mode) ChromaDB, spaCy/Presidio,
        # and attachment processors are never called — no reason to pay their startup RAM cost.
        if Config.ENABLE_RAG:
            logger.info("📚 Initializing documentation vector DB (BookStack)...")
            self.documentation_db = VectorDatabase(
                Config.BOOKSTACK_DB_PATH,
                Config.BOOKSTACK_COLLECTION
            )
            logger.info("📧 Initializing experience vector DB (Past Support)...")
            self.experience_db = VectorDatabase(
                Config.CHROMA_DB_PATH,
                Config.COLLECTION_NAME
            )
            self.vector_db = self.experience_db  # legacy alias

            # Image / table / doc processors only used for attachment RAG context
            self.img_processor = ImageProcessor(ai_agent=self.ai)
            self.tables_processor = TablesProcessor()
            self.doc_processor = DocProcessor(
                image_processor=self.img_processor,
                tables_processor=self.tables_processor
            )

            # PII redactor: only needed to scrub data before RAG ingestion
            from modules.pii_redactor import PIIRedactor
            self.pii_redactor = PIIRedactor()
            logger.info("🔐 PII Redactor initialized (RAG mode)")
        else:
            # Stubs — keep attribute names so ingest_email code paths don't break
            self.documentation_db = None
            self.experience_db = None
            self.vector_db = None
            self.img_processor = None
            self.tables_processor = None
            self.doc_processor = None
            self.pii_redactor = None
            logger.info(
                "⚡ RAG disabled — skipped VectorDB / spaCy / Presidio / ImageProcessor / "
                "TablesProcessor / DocProcessor init (~300-400 MB saved)"
            )
        # Bilal Khan (05/08/2026) - Model unload: skip all heavy ML subsystems when RAG disabled - end

        # 4. Init AI (always available for ticket interpretation/response, independent of RAG)
        self.ai = None
        if Config.OPENAI_API_KEY and Config.AUTO_GENERATE_RESPONSES:
            self.ai = OpenAIAgent(Config.OPENAI_API_KEY)
            self.ai.authenticate()

        # 5. Init Subject Matcher
        self.subject_matcher = SubjectMatcher(similarity_threshold=0.75)

        # 6. Runtime state
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
        Soft-delete functionality has been completely disabled as per requirements.
        
        Returns:
            int: 0
        """
        return 0
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
                    display_id = self.sql.get_display_id(ticket_id)
                    # Soft-delete in SQL only (keep in vector DB for knowledge)
                    if self.sql.soft_delete_ticket(ticket_id):
                        deleted_count += 1
                        logger.info(f"🗑️  Soft-deleted ticket {display_id} in SQL (preserved in knowledge base)")
                    
                except Exception as e:
                    logger.error(f"❌ Failed to soft-delete ticket {ticket_id}: {e}")
            
            logger.info(f"📊 Soft-delete pass complete: {deleted_count} tickets processed")
            return deleted_count
            
        except Exception as e:
            logger.error(f"❌ Soft-delete pass error: {e}")
            return 0
    
    def _hard_delete_pass(self) -> int:
        """
        Hard-delete functionality has been completely disabled as per requirements.
        
        Returns:
            int: 0
        """
        return 0
        try:
            cutoff_dt = datetime.now() - timedelta(days=Config.HARD_DELETE_AFTER_DAYS)
            batch_size = Config.CLEANUP_BATCH_SIZE
            
            # Get candidates
            candidates = self.sql.get_hard_delete_candidates(cutoff_dt, limit=batch_size)
            
            if not candidates:
                logger.debug("ℹ️  No tickets eligible for hard-deletion")
                return 0
            
            deleted_count = 0
            
            # Purge messages and attachments, preserving metrics and logs
            if candidates and not self._shutdown_requested:
                deleted_count = self.sql.purge_heavy_ticket_data(candidates)
            
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
        """Check if email is from internal domain"""
        return self._extract_email_domain(sender) == INTERNAL_DOMAIN.lower()
    
    def find_customer_email(self, email_dict: dict) -> str:
        """
        Identify the real customer email from an email object.
        If sender is staff, looks for the first non-staff recipient in To/CC.
        """
        sender = email_dict.get('sender', '').lower()
        if not self._is_internal_email(sender):
            return sender
            
        # Sender is staff, check TO
        to_str = email_dict.get('to', '').lower()
        recipients = [r.strip() for r in to_str.split(',') if r.strip()]
        for r in recipients:
            if not self._is_internal_email(r):
                return r
                
        # Check CC
        cc_str = email_dict.get('cc', '').lower()
        cc_recipients = [r.strip() for r in cc_str.split(',') if r.strip()]
        for r in cc_recipients:
            if not self._is_internal_email(r):
                return r
                
        return sender # Fallback
    
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
        
        # Idempotency check (Checks both Graph ID and Internet Message ID)
        internet_msg_id = email.get('internet_message_id')
        if self.sql.message_exists(msg_id, internet_msg_id):
            logger.debug(f"ℹ️  Message {msg_id[:20]}... already processed (skipping)")
            return None
        
        # Determine if internal
        is_internal = self._is_internal_email(sender)
        
        # Clean body for internal use, but preserve original for database
        # email['body'] = self._clean_html(email.get('body', '')) # REMOVED: Stripping here breaks dashboard rendering

        # SECURITY: Redact PII before RAG ingestion
        # Bilal Khan (05/08/2026) - Model unload: None-safe PII passthrough when RAG disabled
        if self.pii_redactor is not None:
            redacted_email = self.pii_redactor.redact_email_content(email)
        else:
            # No RAG = no need to scrub; add expected keys so downstream code doesn't KeyError
            redacted_email = dict(email)
            redacted_email.setdefault('pii_redacted', False)
            redacted_email.setdefault('pii_entities_found', 0)
            redacted_email.setdefault('pii_entity_types', [])

        if redacted_email.get('pii_redacted'):
            logger.warning(
                f"🔐 PII redacted | Msg: {msg_id[:20]}... | "
            f"Entities: {redacted_email.get('pii_entities_found', 0)}"
            )
        
        # Create plain text version for RAG and AI (after redaction)
        redacted_email['body_text'] = self._clean_html(redacted_email.get('body', ''))
        # Process attachments (images + docx/pdf + xlsx/csv)
        # NOTE: we store these as "attachment descriptions" in Vector DB so RAG can use them later.
        attachment_descs = []
        processed_attachments = [] # NEW: Buffer to store context until ticket_id is known
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
                    if Config.ENABLE_RAG:
                        # RAG Image Processing (for AI context only)
                        desc = self.img_processor.process_image(att_bytes, filename=name)
                        if desc:
                            attachment_descs.append(desc)
                            # Store in buffer
                            processed_attachments.append({
                                'filename': name,
                                'content_summary': desc,
                                'metadata': {'type': 'image'}
                            })
                        logger.info(f"🖼️  Processed image {name} for RAG context.")
                    else:
                        logger.info(f"🖼️  Image processing skipped (RAG is disabled).")
                            
                elif is_table:
                    # Bilal Khan (05/08/2026) - Model unload: guard table processor behind ENABLE_RAG
                    if Config.ENABLE_RAG and self.tables_processor:
                        res = self.tables_processor.process_bytes(att_bytes, filename=name)
                        txt = (res or {}).get("combined_text", "")
                        if txt:
                            attachment_descs.append(txt)
                            processed_attachments.append({
                                'filename': name,
                                'content_summary': txt[:1000],
                                'metadata': {'type': 'table'}
                            })
                    else:
                        logger.debug(f"⏭️  Table processing skipped (RAG disabled): {name}")
                elif is_doc:
                    # Bilal Khan (05/08/2026) - Model unload: guard doc processor behind ENABLE_RAG
                    if Config.ENABLE_RAG and self.doc_processor:
                        res = self.doc_processor.process_bytes(att_bytes, filename=name)
                        txt = (res or {}).get("combined_text", "")
                        if txt:
                            attachment_descs.append(txt)
                            processed_attachments.append({
                                'filename': name,
                                'content_summary': txt[:1000],
                                'metadata': {'type': 'document'}
                            })
                    else:
                        logger.debug(f"⏭️  Doc processing skipped (RAG disabled): {name}")

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
                # Resolve friendly ID for logging
                display_id = self.sql.get_display_id(ticket_id)
                
                # RE-OPEN LOGIC: If ticket was soft-deleted or closed, re-open it
                if existing.get('deleted_at') or existing.get('status') == 'Closed':
                    logger.info(f"🔄 Ticket {display_id} was {existing.get('status', 'Deleted')}. Re-opening due to new customer email.")
                    self.sql.reopen_ticket(ticket_id)
                    # Update vector DB status
                    self.experience_db.update_ticket_status(ticket_id, False, display_id=display_id)
                
                logger.info(
                    f"🔗 Linked to existing ticket {display_id} | "
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
                    display_id = self.sql.get_display_id(ticket_id)
                    logger.info(
                        f"🔗 Linked to existing ticket {display_id} | "
                        f"Fuzzy match | Sender: {sender}"
                    )
                else:
                    # Create new ticket
                    ticket_id = f"TKT-{msg_id}"
                    customer_email = self.find_customer_email(email)
                    
                    success = self.sql.create_ticket(
                        ticket_id, conv_id, subject, customer_email, actor=sender,
                        source_received_at=email.get('received')
                    )
                    
                    if success:
                        display_id = self.sql.get_display_id(ticket_id)
                        logger.info(
                            f"✨ Created new ticket {display_id} | "
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

            # Store attachment contexts now that ticket_id is confirmed
            for att_ctx in processed_attachments:
                self.sql.store_attachment_context(
                    ticket_id=ticket_id,
                    message_id=msg_id,
                    filename=att_ctx['filename'],
                    content_summary=att_ctx['content_summary'],
                    metadata=att_ctx['metadata']
                )

            # Persist Issue State
            try:
                if self.ai:
                    full_thread = self.sql.get_thread_messages(ticket_id)
                    current_state = self.sql.get_ticket_issue_state(ticket_id)
                    # Original line:
                    # new_state = self.ai.update_issue_state(full_thread, current_state)
                    new_state = self.ai.update_issue_state(full_thread, current_state)
                    self.sql.update_ticket_issue_state(ticket_id, new_state)
                else:
                    logger.info(f"⏭️ Skipping persistent issue state update (AI is disabled)")
            except Exception as e:
                # Use display_id for log if possible
                logger.error(f"Failed to update persistent issue state for {display_id}: {e}")
            
            # Add to vector DB
            if Config.ENABLE_RAG:
                self.experience_db.add_email(
                    email_id=redacted_email['id'],
                    subject=redacted_email['subject'],
                    body=redacted_email.get('body_text', redacted_email['body']),
                    sender=redacted_email['sender'],
                    image_descriptions=attachment_descs,
                    metadata={
                        'ticket_id': ticket_id,
                        'pii_redacted': redacted_email.get('pii_redacted', False),
                        'pii_entities_count': redacted_email.get('pii_entities_found', 0)
                    },
                    display_id=display_id
                )
            else:
                logger.info(f"⏭️ Skipping experience vector DB addition for ticket {display_id} (RAG is disabled).")

        
        else:
            # INTERNAL MESSAGE (Staff Sender)
            
            existing = self.sql.find_ticket_by_conversation_id(conv_id)
            
            if not existing:
                # NEW: Check if support was looped in (To/CC) on a thread involving a customer
                to_str = email.get('to', '').lower()
                cc_str = email.get('cc', '').lower()
                
                if self.user_email.lower() in to_str or self.user_email.lower() in cc_str:
                    customer_email = self.find_customer_email(email)
                    
                    # Only create if we found an actual external customer
                    if not self._is_internal_email(customer_email):
                        logger.info(f"✨ Creating ticket from internal chain | Customer: {customer_email} | Looped in: {self.user_email}")
                        
                        ticket_id = f"TKT-{msg_id}"
                        success = self.sql.create_ticket(
                            ticket_id, conv_id, subject, customer_email, actor=sender,
                            source_received_at=email.get('received')
                        )
                        if success:
                            # Re-fetch the newly created ticket info
                            existing = self.sql.find_ticket_by_conversation_id(conv_id)
            
            if existing:
                ticket_id = existing['ticket_id']
                
                # Resolve friendly ID for logging
                display_id = self.sql.get_display_id(ticket_id)
                
                # RE-OPEN LOGIC: Even for internal replies, we should clear soft-delete or closed status
                if existing.get('deleted_at') or existing.get('status') == 'Closed':
                    logger.info(f"🔄 Ticket {display_id} was {existing.get('status', 'Deleted')}. Restoring due to internal reply.")
                    self.sql.reopen_ticket(ticket_id)
                    # Update vector DB status
                    self.experience_db.update_ticket_status(ticket_id, False, display_id=display_id)

                logger.info(
                    f"📨 Internal reply to ticket {display_id} | "
                    f"Sender: {sender}"
                )
                
                # Log message
                self.sql.log_message(ticket_id, email, is_internal=True)

                # Store attachment contexts now that ticket_id is confirmed
                for att_ctx in processed_attachments:
                    self.sql.store_attachment_context(
                        ticket_id=ticket_id,
                        message_id=msg_id,
                        filename=att_ctx['filename'],
                        content_summary=att_ctx['content_summary'],
                        metadata=att_ctx['metadata']
                    )

                # Persist Issue State (Internal replies also update context)
                try:
                    if self.ai:
                        full_thread = self.sql.get_thread_messages(ticket_id)
                        current_state = self.sql.get_ticket_issue_state(ticket_id)
                        # Original line:
                        # new_state = self.ai.update_issue_state(full_thread, current_state)
                        new_state = self.ai.update_issue_state(full_thread, current_state)
                        self.sql.update_ticket_issue_state(ticket_id, new_state)
                    else:
                        logger.info(f"⏭️ Skipping persistent issue state update (AI is disabled) for ticket {ticket_id}")
                except Exception as e:
                    logger.error(f"Failed to update persistent issue state for {ticket_id}: {e}")
                
                # Add to vector DB (for RAG context)
                if Config.ENABLE_RAG:
                    self.experience_db.add_email(
                        email_id=redacted_email['id'],
                        subject=redacted_email['subject'],
                        body=redacted_email.get('body_text', redacted_email['body']),
                        sender=redacted_email['sender'],
                        image_descriptions=attachment_descs,
                        metadata={
                            'ticket_id': ticket_id,
                            'pii_redacted': redacted_email.get('pii_redacted', False),
                            'pii_entities_count': redacted_email.get('pii_entities_found', 0)
                        },
                        display_id=display_id
                    )
                else:
                    logger.info(f"⏭️ Skipping experience vector DB addition for ticket {display_id} (RAG is disabled).")

            else:
                # Check if this orphan internal email was sent to support (to create a ticket)
                is_direct_to_support = self.user_email and self.user_email.lower() in email.get('to', '').lower()
                
                if is_direct_to_support:
                    # Create new ticket
                    ticket_id = f"TKT-{msg_id}"
                    customer_email = self.find_customer_email(email)
                    
                    success = self.sql.create_ticket(
                        ticket_id, conv_id, subject, customer_email, actor=sender
                    )
                    
                    if success:
                        display_id = self.sql.get_display_id(ticket_id)
                        logger.info(
                            f"✨ Created new ticket {display_id} from internal support request | "
                            f"Staff: {sender} | Subject: {subject[:50]}"
                        )
                        
                        # Log message (as non-internal for the initial ticket entry so it shows up)
                        self.sql.log_message(ticket_id, email, is_internal=False)
                        
                        # Store attachment contexts
                        for att_ctx in processed_attachments:
                            self.sql.store_attachment_context(
                                ticket_id=ticket_id,
                                message_id=msg_id,
                                filename=att_ctx['filename'],
                                content_summary=att_ctx['content_summary'],
                                metadata=att_ctx['metadata']
                            )
                        
                        # Add to vector DB
                        if Config.ENABLE_RAG:
                            self.experience_db.add_email(
                                email_id=redacted_email['id'],
                                subject=redacted_email['subject'],
                                body=redacted_email.get('body_text', redacted_email['body']),
                                sender=redacted_email['sender'],
                                image_descriptions=attachment_descs,
                                metadata={
                                    'ticket_id': ticket_id,
                                    'pii_redacted': redacted_email.get('pii_redacted', False),
                                    'pii_entities_count': redacted_email.get('pii_entities_found', 0)
                                },
                                display_id=display_id
                            )
                        else:
                            logger.info(f"⏭️ Skipping experience vector DB addition for ticket {display_id} (RAG is disabled).")
                    else:
                        logger.error(
                            f"❌ Failed to create ticket from internal support request | "
                            f"Msg: {msg_id[:20]}... | Sender: {sender}"
                        )
                        return None
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
        # AI response generation requires RAG context, skip if disabled
        if not Config.ENABLE_RAG:
            logger.info(f"⏭️ Skipping AI response draft generation for ticket {ticket_id} (RAG is disabled).")
            return

        # Resolve friendly ID early for all function logs
        display_id = self.sql.get_display_id(ticket_id)

        # 1. Fetch thread
        messages = self.sql.get_thread_messages(ticket_id)
        if not messages:
            logger.warning(f"⚠️  No messages found for ticket {display_id}")
            return
        
        # 2. Check last speaker
        last_msg = messages[-1]
        if last_msg['is_internal'] == 1:
            logger.info(
                f"🛑 Ticket {display_id}: Support replied last. No AI needed."
            )
            return
        
        logger.info(f"🧠 Stage 1: Interpreting ticket {display_id}...")
        
        # 2b. INTERPRETATION LAYER
        interpretation = self.ai.interpret_message(
            last_msg.get('body_text', ''), 
            messages[0].get('subject', '')
        )
        intent = interpretation.get('intent', 'OTHER')
        summary = interpretation.get('summary', '')
        urgency = interpretation.get('urgency', 'MEDIUM')
        
        logger.info(f"🔍 Result: Intent={intent} | Urgency={urgency} | Summary={summary}")

        # 2c. PERSISTENT STATE RETRIEVAL
        issue_state = self.sql.get_ticket_issue_state(ticket_id) or {}
        problem_summary = issue_state.get('problem_summary', summary)
        technical_signals = issue_state.get('technical_signals', '')
        error_codes = ", ".join(issue_state.get('error_codes', []))

        # 2d. ATTACHMENT CONTEXT RETRIEVAL
        attach_contexts = self.sql.get_ticket_attachments_context(ticket_id)
        attach_summary = ""
        if attach_contexts:
            attach_summary = "\nATTACHMENT CONTEXT FOUND:\n" + "\n".join([
                f"- {a['filename']}: {a['content_summary']}" for a in attach_contexts
            ])

        logger.info(f"🧠 Stage 2: Generating AI Draft for ticket {display_id}...")
        
        # 3. DUAL RAG SEARCH
        # Consolidated query using Persistent State + Current Message
        consolidated_query = f"{problem_summary} {technical_signals} {error_codes}".strip()
        rag_query = consolidated_query if len(consolidated_query) > 10 else last_msg['body_text']
        
        # Add current message context if it's very different from the summary
        if summary and problem_summary and self.subject_matcher.calculate_similarity(summary, problem_summary) < 0.5:
             rag_query += f" CURRENT UPDATE: {summary}"
        
        # 3a. Search documentation (authoritative)
        docs = self.documentation_db.search_similar(
            rag_query, 
            top_k=Config.TOP_K_RESULTS
        )
        logger.info(f"📚 Documentation search: {len(docs)} chunks found")
        
        # 3b. Search experience (advisory)
        experiences = self.experience_db.search_similar(
            rag_query, 
            top_k=Config.TOP_K_RESULTS
        )
        logger.info(f"📧 Experience search: {len(experiences)} past cases found")
        
        # Resolve friendly ID for logging and passing to AI
        display_id = self.sql.get_display_id(ticket_id)

        # 4. Generate response with SEPARATE contexts and INTENT routing
        draft, provenance = self.ai.generate_response(
            messages=messages,
            documentation_context=docs,      
            experience_context=experiences,   
            customer_email=messages[0].get('sender'),
            intent=intent,
            summary=f"{summary}\n{attach_summary}", # Include attachment info in summary context
            display_id=display_id
        )
        
        # 5. Save draft with combined provenance
        if draft:
            self.sql.update_ai_draft(ticket_id, draft, provenance)
            
            if provenance:
                # Break down provenance by source
                doc_prov = [p for p in provenance if p.get('source') == 'documentation']
                exp_prov = [p for p in provenance if p.get('source') == 'experience']
                
                logger.info(
                    f"✅ SUCCESS: AI Response Generated successfully for ticket {display_id} | "
                    f"Docs: {len(doc_prov)} | Exp: {len(exp_prov)}"
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
                logger.info(f"✅ SUCCESS: AI Response Generated successfully for ticket {display_id} (no context)")
        else:
            logger.error(f"❌ Failed to generate draft for {display_id}")
    
    # Bilal Khan (12/08/2026) Issue No  Sheet_Name  - Helper to touch worker_heartbeat.txt for Docker healthcheck
    def _update_heartbeat(self):
        try:
            os.makedirs("data", exist_ok=True)
            with open(os.path.join("data", "worker_heartbeat.txt"), "w") as f:
                f.write(str(time.time()))
        except Exception as hb_err:
            logger.debug(f"Failed to touch heartbeat file: {hb_err}")

    def run_backlog(self, days: int = 7):
        """
        Process backlog emails in memory-safe streaming batches.

        Instead of loading all N emails into memory at once, this fetches
        BACKLOG_BATCH_SIZE emails at a time using a sliding time-window anchor
        (oldest-first). Each batch is fully processed and then explicitly freed
        before the next fetch, keeping peak email-list memory at ~5-10 MB
        rather than ~100 MB for a 500-email backlog.

        Args:
            days: Number of days to look back
        """
        # Bilal Khan (12/08/2026) - P1 Fix #7: Streaming batch backlog processing - start
        if days <= 0:
            logger.info("⏭️ Backlog processing skipped (PROCESSING_DAYS_BACK=0)")
            return

        logger.info("=" * 80)
        logger.info(f"Processing backlog ({days} days) in streaming batches")
        logger.info("=" * 80)

        # Configurable batch size — small enough to keep memory low, large enough
        # to minimise Graph API round-trips (max 500 per call, we cap at 50).
        BACKLOG_BATCH_SIZE = getattr(Config, 'BACKLOG_BATCH_SIZE', 50)

        touched = set()
        total_success = 0
        total_errors = 0
        total_fetched = 0

        # We slide the window forward: start from `days` ago, advance the
        # anchor each batch so we don't re-fetch already-seen messages.
        # Graph API doesn't support true offset pagination on filter+orderby
        # without a nextLink, so we approximate by shrinking the time window
        # after each batch using the timestamp of the last email processed.
        window_start_minutes = days * 24 * 60  # minutes back from now
        MIN_WINDOW_MINUTES = 10  # stop once window shrinks below poll interval

        batch_num = 0
        while window_start_minutes > MIN_WINDOW_MINUTES:
            if self._shutdown_requested:
                logger.info("🛑 Shutdown requested, stopping backlog processing")
                break

            batch_num += 1
            logger.info(
                f"📦 Backlog batch #{batch_num} — window: last {window_start_minutes:.0f} min "
                f"| batch_size: {BACKLOG_BATCH_SIZE}"
            )

            batch = self.graph.fetch_latest_emails(
                self.user_email,
                minutes=int(window_start_minutes),
                top=BACKLOG_BATCH_SIZE
            )

            if not batch:
                logger.info("ℹ️  No emails in this window, backlog complete.")
                break

            # Sort oldest first within this batch
            batch.sort(key=lambda x: x['received'])
            total_fetched += len(batch)

            # Advance anchor to just before the oldest email in this batch
            # so the next fetch window doesn't overlap significantly.
            # The anchor moves forward by the age of the newest email in the batch.
            newest_received = batch[-1]['received']  # ISO string
            try:
                from datetime import timezone
                newest_dt = datetime.fromisoformat(newest_received.replace('Z', '+00:00'))
                now_utc = datetime.now(timezone.utc)
                age_minutes = (now_utc - newest_dt).total_seconds() / 60
                # Shrink window to just before the newest email we just saw.
                # Add a 2-minute overlap to avoid missing emails at boundary.
                new_window = max(age_minutes - 2, MIN_WINDOW_MINUTES)
                if new_window >= window_start_minutes:
                    # No progress — the API returned stale data; avoid infinite loop
                    logger.warning(
                        f"⚠️  Backlog window didn't advance "
                        f"(new={new_window:.0f}min >= old={window_start_minutes:.0f}min). "
                        "Breaking to avoid loop."
                    )
                    # Process this batch then stop
                    window_start_minutes = 0
                else:
                    window_start_minutes = new_window
            except Exception as parse_err:
                logger.warning(f"⚠️  Could not parse newest email timestamp: {parse_err}. Stopping backlog.")
                window_start_minutes = 0

            # --- Process this batch ---
            batch_success = 0
            batch_errors = 0
            for i, email in enumerate(batch):
                if self._shutdown_requested:
                    break
                self._update_heartbeat()
                try:
                    ticket_id = self.ingest_email(email)
                    if ticket_id:
                        touched.add(ticket_id)
                        batch_success += 1
                    if (i + 1) % 10 == 0:
                        logger.info(
                            f"📊 Batch #{batch_num} progress: {i + 1}/{len(batch)} | "
                            f"Success: {batch_success} | Errors: {batch_errors}"
                        )
                except Exception as e:
                    batch_errors += 1
                    logger.error(
                        f"❌ Error processing email | "
                        f"ID: {email.get('id', 'unknown')[:20]}... | "
                        f"Sender: {email.get('sender', 'unknown')}: {e}"
                    )

            total_success += batch_success
            total_errors += batch_errors
            logger.info(
                f"✅ Batch #{batch_num} complete | "
                f"Fetched: {len(batch)} | Success: {batch_success} | Errors: {batch_errors} | "
                f"Total fetched so far: {total_fetched}"
            )

            # --- Free batch memory before next fetch ---
            del batch
            gc.collect()

            # If we got fewer emails than the batch size the API has no more to offer
            if len(touched) == 0 and batch_success == 0 and batch_num == 1:
                # Likely all already processed (idempotent check passed)
                logger.info("ℹ️  All backlog emails already processed (idempotent).")
                break
        # Bilal Khan (12/08/2026) - P1 Fix #7: Streaming batch backlog processing - end

        # Generate AI responses for all newly touched tickets
        logger.info(f"🧠 Generating AI for {len(touched)} active tickets...")

        ai_success = 0
        ai_errors = 0

        for ticket_id in touched:
            if self._shutdown_requested:
                break
            self._update_heartbeat()
            try:
                self.generate_ai_response(ticket_id)
                ai_success += 1
            except Exception as e:
                ai_errors += 1
                err_id = self.sql.get_display_id(ticket_id)
                logger.error(f"❌ Error generating AI for {err_id}: {e}")

        logger.info("=" * 80)
        logger.info("Backlog processing complete")
        logger.info(f"📊 Email ingestion: {total_success} success, {total_errors} errors ({total_fetched} fetched)")
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
                
                # Bilal Khan (05/08/2026) - Wave 1B: Heartbeat moved here (before AI generation) - start
                # Graph API call succeeded - touch heartbeat immediately.
                # AI generation below can take several minutes for multiple tickets,
                # which would cause Docker healthcheck to falsely kill a healthy container.
                self._update_heartbeat()
                # Bilal Khan (05/08/2026) - Wave 1B: Heartbeat moved here (before AI generation) - end
                
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
                
                # Process AI Drafts for new emails only (Regeneration is now handled synchronously by the dashboard)
                # Cleanup any accidental legacy flags
                legacy_regen = self.sql.get_tickets_needing_regeneration()
                if legacy_regen:
                    logger.info(f"🧹 Clearing {len(legacy_regen)} legacy AI regeneration flags...")
                    for t_id in legacy_regen:
                        with self.sql.conn_manager.get_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("UPDATE tickets SET needs_ai_generation = FALSE WHERE ticket_id = %s", (t_id,))

                # Initial AI processing for new emails
                for ticket_id in touched:
                    if self._shutdown_requested:
                        break
                    try:
                        self.generate_ai_response(ticket_id)
                    except Exception as e:
                        logger.error(f"❌ Error generating AI for {ticket_id}: {e}")
                
                # Bilal Khan (05/08/2026) - Wave 1B: Old heartbeat block removed from here (moved above AI generation)
                # Sleep until next poll
                if not self._shutdown_requested:
                    time.sleep(Config.POLLING_INTERVAL)
                # Bilal Khan (12/08/2026) - P1 Fix #7: Explicit GC after each poll cycle to reclaim
                # BeautifulSoup DOM trees and Azure SDK HTTP response objects promptly.
                gc.collect()
            
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