"""
Microsoft Graph Connector with Resilient Retry Logic

Improvements:
1. Exponential backoff with jitter for all Graph API calls
2. Automatic retry for transient failures
3. Enhanced error logging with operation context
4. Graceful degradation on persistent failures
5. Maintains isolated asyncio worker design
"""

from azure.identity import ClientSecretCredential
from msgraph import GraphServiceClient
from msgraph.generated.users.item.messages.messages_request_builder import MessagesRequestBuilder
from typing import List, Dict, Optional, Any, Callable
import logging
import asyncio
import base64
import threading
import queue
import time
import random
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] %(message)s'
)
logger = logging.getLogger(__name__)


class RequestType(Enum):
    """Types of requests that can be sent to the asyncio worker"""
    FETCH_EMAILS = "fetch_emails"
    GET_ATTACHMENT = "get_attachment"
    SEND_EMAIL = "send_email"
    REPLY_TO_EMAIL = "reply_to_email"
    SHUTDOWN = "shutdown"


@dataclass
class GraphRequest:
    """Request object sent to asyncio worker thread"""
    request_type: RequestType
    params: dict
    response_queue: queue.Queue


@dataclass
class GraphResponse:
    """Response object returned from asyncio worker thread"""
    success: bool
    data: Any = None
    error: Optional[str] = None


class RetryConfig:
    """Configuration for retry logic with exponential backoff"""
    
    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        exponential_base: float = 2.0,
        jitter: bool = True
    ):
        """
        Initialize retry configuration.
        
        Args:
            max_retries: Maximum number of retry attempts
            base_delay: Base delay in seconds
            max_delay: Maximum delay in seconds
            exponential_base: Base for exponential backoff
            jitter: Whether to add random jitter to delay
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
    
    def get_delay(self, attempt: int) -> float:
        """
        Calculate delay for a given attempt with exponential backoff.
        
        Args:
            attempt: Attempt number (0-indexed)
            
        Returns:
            Delay in seconds
        """
        # Calculate exponential delay
        delay = min(
            self.base_delay * (self.exponential_base ** attempt),
            self.max_delay
        )
        
        # Add jitter (±25% randomness)
        if self.jitter:
            jitter_range = delay * 0.25
            delay += random.uniform(-jitter_range, jitter_range)
        
        return max(0, delay)


# Bilal Khan (12/08/2026) - P1 Fix #5: Circuit-breaker auth detection - start
_AUTH_ERROR_PATTERNS = (
    "401",
    "unauthorized",
    "invalid_client",
    "authentication_failed",
    "client secret",
    "credentials",
    "access token",
    "token is expired",
)

def _is_auth_error(exc: Exception) -> bool:
    """
    Returns True if the exception looks like an Azure authentication
    failure (expired/invalid credentials) vs a transient network error.
    Auth errors should NOT be retried — they will never succeed until
    the secret is rotated.
    """
    msg = str(exc).lower()
    return any(pat in msg for pat in _AUTH_ERROR_PATTERNS)
# Bilal Khan (12/08/2026) - P1 Fix #5: Circuit-breaker auth detection - end


async def retry_with_backoff(
    operation: Callable,
    config: RetryConfig,
    operation_name: str = "operation"
):
    """
    Retry an async operation with exponential backoff.

    Auth errors (401 / invalid_client) are NOT retried — they break
    immediately and re-raise so the circuit-breaker in GraphConnector
    can count consecutive failures and open the circuit.

    Args:
        operation: Async callable to retry
        config: Retry configuration
        operation_name: Name for logging

    Returns:
        Result of operation

    Raises:
        Exception: If all retries fail
    """
    last_exception = None

    for attempt in range(config.max_retries + 1):
        try:
            result = await operation()

            if attempt > 0:
                logger.info(f"✅ {operation_name} succeeded on attempt {attempt + 1}")

            return result

        except Exception as e:
            last_exception = e

            # Bilal Khan (12/08/2026) - P1 Fix #5: Break immediately on auth errors, no retry
            if _is_auth_error(e):
                logger.error(
                    f"🔐 {operation_name} failed with authentication error (no retry): {e}"
                )
                break

            # Don't retry on final attempt
            if attempt >= config.max_retries:
                logger.error(
                    f"❌ {operation_name} failed after {config.max_retries + 1} attempts: {e}"
                )
                break

            # Calculate delay
            delay = config.get_delay(attempt)

            logger.warning(
                f"⚠️  {operation_name} failed (attempt {attempt + 1}/{config.max_retries + 1}): {e}. "
                f"Retrying in {delay:.2f}s..."
            )

            # Wait before retry
            await asyncio.sleep(delay)

    # All retries failed
    raise last_exception


class AsyncioWorker:
    """
    Dedicated asyncio worker thread for Graph API calls with retry logic.
    
    Design principles:
    - Owns its own event loop
    - Automatic retry with exponential backoff
    - Clean shutdown mechanism
    - Exception isolation
    """
    
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        tenant_id: str,
        retry_config: Optional[RetryConfig] = None
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id
        
        # Retry configuration
        self.retry_config = retry_config or RetryConfig(
            max_retries=3,
            base_delay=1.0,
            max_delay=30.0
        )
        
        # Thread-safe queue for incoming requests
        self.request_queue: queue.Queue = queue.Queue()
        
        # Worker thread and loop
        self.worker_thread: Optional[threading.Thread] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.client: Optional[GraphServiceClient] = None
        
        # Shutdown flag
        self._shutdown = threading.Event()
        self._started = threading.Event()
    
    def start(self):
        """Start the asyncio worker thread"""
        if self.worker_thread and self.worker_thread.is_alive():
            logger.warning("⚠️  AsyncioWorker already running")
            return
        
        self._shutdown.clear()
        self.worker_thread = threading.Thread(
            target=self._run_event_loop,
            name="GraphAsyncioWorker",
            daemon=True
        )
        self.worker_thread.start()
        
        if not self._started.wait(timeout=10):
            raise RuntimeError("❌ AsyncioWorker failed to start within 10 seconds")
        
        logger.info("✅ AsyncioWorker started successfully")
    
    def _run_event_loop(self):
        """Worker thread main loop with retry support"""
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self._initialize_client()
            self._started.set()
            
            while not self._shutdown.is_set():
                try:
                    request = self.request_queue.get(timeout=0.5)
                    
                    if request.request_type == RequestType.SHUTDOWN:
                        logger.info("🛑 Shutdown request received")
                        break
                    
                    self._process_request(request)
                    
                except queue.Empty:
                    continue
                except Exception as e:
                    logger.error(f"❌ Error processing request: {e}")
        
        finally:
            logger.info("🧹 Shutting down AsyncioWorker event loop")
            try:
                if self.loop and self.loop.is_running():
                    pending = asyncio.all_tasks(self.loop)
                    for task in pending:
                        task.cancel()
                
                if self.loop:
                    self.loop.close()
            except Exception as e:
                logger.error(f"❌ Error during loop cleanup: {e}")
    
    def _initialize_client(self):
        """Initialize Graph client (called within asyncio thread)"""
        try:
            credential = ClientSecretCredential(
                tenant_id=self.tenant_id,
                client_id=self.client_id,
                client_secret=self.client_secret
            )
            scopes = ['https://graph.microsoft.com/.default']
            self.client = GraphServiceClient(credentials=credential, scopes=scopes)
            logger.info("✅ Graph client initialized in worker thread")
        except Exception as e:
            logger.error(f"❌ Failed to initialize Graph client: {e}")
            raise
    
    def _process_request(self, request: GraphRequest):
        """
        Process a single request with retry logic.
        
        Error handling: All exceptions are caught and returned as GraphResponse
        """
        try:
            if request.request_type == RequestType.FETCH_EMAILS:
                # Bilal Khan (05/08/2026) - Wave 1A: Timeout covers full retry cycle (3x30s per attempt + jitter)
                result = self.loop.run_until_complete(
                    asyncio.wait_for(
                        retry_with_backoff(
                            lambda: self._fetch_emails_async(**request.params),
                            self.retry_config,
                            f"fetch_emails({request.params.get('user_email', 'unknown')})"
                        ),
                        timeout=120.0
                    )
                )
                response = GraphResponse(success=True, data=result)
            
            elif request.request_type == RequestType.GET_ATTACHMENT:
                result = self.loop.run_until_complete(
                    asyncio.wait_for(
                        retry_with_backoff(
                            lambda: self._get_attachment_async(**request.params),
                            self.retry_config,
                            f"get_attachment({request.params.get('attachment_id', 'unknown')[:20]}...)"
                        ),
                        # Bilal Khan (12/08/2026) - P0 Fix #1: Reduced from 60s → 25s.
                        # Outer queue.get() in get_attachment_sync is 30s; inner MUST be
                        # shorter so the worker always puts a GraphResponse on the queue
                        # before the caller times out. 60s > 30s was causing the caller to
                        # abandon the request while the async coroutine kept running,
                        # orphaning SSL contexts and HTTP buffers in memory each poll cycle.
                        timeout=25.0
                    )
                )
                response = GraphResponse(success=True, data=result)

            elif request.request_type == RequestType.SEND_EMAIL:
                result = self.loop.run_until_complete(
                    asyncio.wait_for(
                        retry_with_backoff(
                            lambda: self._send_email_async(**request.params),
                            self.retry_config,
                            "send_email"
                        ),
                        timeout=120.0  # Bilal Khan (05/08/2026) - Wave 1A: was 45s, increased to cover retries
                    )
                )
                response = GraphResponse(success=True, data=result)
            
            elif request.request_type == RequestType.REPLY_TO_EMAIL:
                result = self.loop.run_until_complete(
                    asyncio.wait_for(
                        retry_with_backoff(
                            lambda: self._reply_to_email_async(**request.params),
                            self.retry_config,
                            "reply_to_email"
                        ),
                        timeout=120.0  # Bilal Khan (05/08/2026) - Wave 1A: was 45s, increased to cover retries
                    )
                )
                response = GraphResponse(success=True, data=result)
            
            else:
                response = GraphResponse(
                    success=False,
                    error=f"Unknown request type: {request.request_type}"
                )
        
        except Exception as e:
            logger.error(f"❌ Request failed after retries: {e}")
            response = GraphResponse(success=False, error=str(e))
        
        request.response_queue.put(response)
    
    async def _fetch_emails_async(self, user_email: str, minutes: int, top: int) -> List[Dict]:
        """Async implementation of email fetching"""
        try:
            time_threshold = (datetime.utcnow() - timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%SZ')
            filter_query = f"receivedDateTime ge {time_threshold}"
            
            query_params = MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters(
                filter=filter_query,
                select=['id', 'conversationId', 'subject', 'body', 'uniqueBody', 'bodyPreview', 'from', 'receivedDateTime', 'hasAttachments', 'ccRecipients', 'bccRecipients', 'toRecipients', 'internetMessageId'],
                orderby=['receivedDateTime ASC'],
                top=top,
                expand=['attachments']
            )
            
            request_config = MessagesRequestBuilder.MessagesRequestBuilderGetRequestConfiguration(
                query_parameters=query_params
            )
            
            emails = []
            messages = await self.client.users.by_user_id(user_email).messages.get(
                request_configuration=request_config
            )
            
            while messages and messages.value:
                for msg in messages.value:
                    # FALLBACK: Fetch attachments if missing but referenced in body or has_attachments is true
                    # Some clients don't set has_attachments flag for inline images
                    body_content = getattr(msg.body, 'content', '') if msg.body else ''
                    has_cid = 'cid:' in body_content
                    if (getattr(msg, 'has_attachments', False) or has_cid) and (not hasattr(msg, 'attachments') or not msg.attachments):
                        try:
                            logger.info(f"🔍 Fetching missing attachments for message {msg.id[:10]}...")
                            att_resp = await self.client.users.by_user_id(user_email).messages.by_message_id(msg.id).attachments.get()
                            if att_resp and att_resp.value:
                                msg.attachments = att_resp.value
                                logger.info(f"✅ Found {len(msg.attachments)} missing attachments")
                        except Exception as e:
                            logger.warning(f"⚠️ Failed to fetch missing attachments for {msg.id[:10]}: {e}")

                    emails.append(self._parse_email(msg))
                
                # Pagination
                next_link = getattr(messages, 'odata_next_link', None)
                if not next_link:
                    break
                messages = await self.client.users.by_user_id(user_email).messages.with_url(next_link).get()
            
            logger.info(f"📧 Fetched {len(emails)} emails for {user_email} (last {minutes} minutes)")
            return emails
        
        except Exception as e:
            logger.error(f"❌ Async fetch failed for {user_email}: {e}")
            raise
    
    async def _get_attachment_async(self, user_email: str, message_id: str, attachment_id: str):
        """Async implementation of attachment download"""
        try:
            attachment = await self.client.users.by_user_id(user_email)\
                .messages.by_message_id(message_id)\
                .attachments.by_attachment_id(attachment_id).get()
            
            if hasattr(attachment, 'content_bytes') and attachment.content_bytes:
                content = attachment.content_bytes
                
                # Case 1: content is str -> definitely Base64
                if isinstance(content, str):
                    try:
                        decoded = base64.b64decode(content)
                        logger.info(
                            f"📎 Decoded Base64 string attachment {attachment_id[:20]}... "
                            f"({len(decoded)} bytes)"
                        )
                        return decoded
                    except Exception as e:
                        logger.warning(f"⚠️ Failed to decode Base64 string: {e}")
                        return content.encode('utf-8')

                # Case 2: content is bytes -> Check if it's actually Base64 bytes
                if isinstance(content, bytes):
                    try:
                        # Optimization: Real binary (images/PDF) almost always contains non-ASCII bytes.
                        # If decode('ascii') fails, it is definitely RAW BINARY.
                        content_str = content.decode('ascii')
                        
                        # If we are here, the content is 100% ASCII. 
                        # It is extremely likely to be Base64 encoded binary.
                        # We use validate=True to ensure we don't corrupt plain text files.
                        decoded = base64.b64decode(content_str, validate=True)
                        
                        logger.info(
                            f"📎 Decoded Base64 bytes attachment {attachment_id[:20]}... "
                            f"({len(decoded)} bytes)"
                        )
                        return decoded
                    except (UnicodeDecodeError, base64.binascii.Error):
                        # UnicodeDecodeError: Contains non-ASCII -> Raw Binary -> Keep as is
                        # binascii.Error: Not valid Base64 -> Raw Binary -> Keep as is
                        pass

                logger.info(
                    f"📎 Downloaded attachment {attachment_id[:20]}... "
                    f"({len(content)} bytes)"
                )
                return content
            
            logger.warning(f"⚠️  Attachment {attachment_id[:20]}... has no content")
            return None
        
        except Exception as e:
            logger.warning(f"⚠️  Failed to download attachment {attachment_id[:20]}...: {e}")
            raise
    
    def _parse_email(self, msg) -> Dict:
        """Convert Graph Object to Dictionary (synchronous helper)"""
        attachments = []
        if hasattr(msg, 'attachments') and msg.attachments:
            for att in msg.attachments:
                if getattr(att, 'odata_type', '') == '#microsoft.graph.itemAttachment':
                    continue
                attachments.append({
                    'id': getattr(att, 'id', ''),
                    'name': getattr(att, 'name', 'unknown'),
                    'content_type': getattr(att, 'content_type', ''),
                    'size': getattr(att, 'size', 0),
                    'is_inline': getattr(att, 'is_inline', False),
                    'content_id': getattr(att, 'content_id', None)
                })
        
        sender_email = "unknown"
        if hasattr(msg, 'from_') and msg.from_ and msg.from_.email_address:
            sender_email = msg.from_.email_address.address
        
        # Use uniqueBody for a cleaner body if available, otherwise fallback to full body
        primary_html = msg.unique_body.content if (hasattr(msg, 'unique_body') and msg.unique_body) else None
        if not primary_html:
            primary_html = msg.body.content if (hasattr(msg, 'body') and msg.body) else ''
            
        return {
            'id': msg.id,
            'internet_message_id': getattr(msg, 'internet_message_id', None),
            'conversation_id': getattr(msg, 'conversation_id', msg.id),
            'subject': getattr(msg, 'subject', 'No Subject'),
            'body_html': primary_html,
            'body_text': getattr(msg, 'body_preview', primary_html[:255]),
            'body': primary_html, # Deprecated legacy support
            'sender': sender_email,
            'received': msg.received_date_time.isoformat() if hasattr(msg, 'received_date_time') else datetime.now().isoformat(),
            'attachments': attachments,
            'to': ', '.join([r.email_address.address for r in msg.to_recipients if r.email_address]) if hasattr(msg, 'to_recipients') and msg.to_recipients else '',
            'cc': ', '.join([r.email_address.address for r in msg.cc_recipients if r.email_address]) if hasattr(msg, 'cc_recipients') and msg.cc_recipients else '',
            'bcc': ', '.join([r.email_address.address for r in msg.bcc_recipients if r.email_address]) if hasattr(msg, 'bcc_recipients') and msg.bcc_recipients else ''
        }
    
    async def _send_email_async(
        self,
        user_email: str,
        to_email: str,
        subject: str,
        body_html: str,
        conversation_id: str = None,
        cc: str = "",
        bcc: str = "",
        attachments: list = None
    ) -> bool:
        from msgraph.generated.users.item.send_mail.send_mail_post_request_body import SendMailPostRequestBody
        from msgraph.generated.models.message import Message
        from msgraph.generated.models.item_body import ItemBody
        from msgraph.generated.models.body_type import BodyType
        from msgraph.generated.models.recipient import Recipient
        from msgraph.generated.models.email_address import EmailAddress
        from msgraph.generated.models.file_attachment import FileAttachment

        message = Message()
        message.subject = subject

        message.body = ItemBody()
        message.body.content_type = BodyType.Html
        message.body.content = body_html

        if conversation_id:
            message.conversation_id = conversation_id

        def make_recipient(email):
            r = Recipient()
            r.email_address = EmailAddress()
            r.email_address.address = email
            return r

        message.to_recipients = [make_recipient(to_email)]

        if cc:
            # Support both comma and semicolon separators
            cc_list = cc.replace(';', ',').split(',')
            message.cc_recipients = [make_recipient(e.strip()) for e in cc_list if e.strip()]
        
        if bcc:
            # Support both comma and semicolon separators
            bcc_list = bcc.replace(';', ',').split(',')
            message.bcc_recipients = [make_recipient(e.strip()) for e in bcc_list if e.strip()]

        if attachments:
            files = []
            for att in attachments:
                fa = FileAttachment()
                fa.name = att['name']
                fa.content_type = att['contentType']
                fa.content_bytes = base64.b64decode(att['content'])
                if att.get('is_inline') or att.get('isInline'):
                    fa.is_inline = True
                    fa.content_id = att.get('content_id') or att.get('contentId')
                files.append(fa)
            message.attachments = files

        body = SendMailPostRequestBody()
        body.message = message
        body.save_to_sent_items = True

        await self.client.users.by_user_id(user_email).send_mail.post(body=body)
        return True

    async def _reply_to_email_async(
        self,
        user_email: str,
        parent_message_id: str,
        body_html: str,
        to_email: str = None,
        cc: str = "",
        bcc: str = "",
        attachments: list = None
    ) -> bool:
        """
        Reply to an existing message using the Graph API's dedicated reply endpoint.
        This automatically handles In-Reply-To, References, and Subject (Re:).
        """
        from msgraph.generated.users.item.messages.item.reply.reply_post_request_body import ReplyPostRequestBody
        from msgraph.generated.models.message import Message
        from msgraph.generated.models.item_body import ItemBody
        from msgraph.generated.models.body_type import BodyType
        from msgraph.generated.models.recipient import Recipient
        from msgraph.generated.models.email_address import EmailAddress
        from msgraph.generated.models.file_attachment import FileAttachment

        # 1. Create the reply message structure
        reply_message = Message()
        reply_message.body = ItemBody()
        reply_message.body.content_type = BodyType.Html
        reply_message.body.content = body_html

        # 2. Add Recipients (To, CC, BCC)
        def make_recipient(email):
            r = Recipient()
            r.email_address = EmailAddress()
            r.email_address.address = email
            return r

        if to_email:
            reply_message.to_recipients = [make_recipient(to_email)]

        if cc:
            # Support both comma and semicolon separators
            cc_list = cc.replace(';', ',').split(',')
            reply_message.cc_recipients = [make_recipient(e.strip()) for e in cc_list if e.strip()]

        if bcc:
            # Support both comma and semicolon separators
            bcc_list = bcc.replace(';', ',').split(',')
            reply_message.bcc_recipients = [make_recipient(e.strip()) for e in bcc_list if e.strip()]

        # 3. Add attachments if provided
        if attachments:
            files = []
            for att in attachments:
                fa = FileAttachment()
                fa.name = att['name']
                fa.content_type = att['contentType']
                fa.content_bytes = base64.b64decode(att['content'])
                if att.get('is_inline') or att.get('isInline'):
                    fa.is_inline = True
                    fa.content_id = att.get('content_id') or att.get('contentId')
                files.append(fa)
            reply_message.attachments = files

        # 4. Prepare the request body
        request_body = ReplyPostRequestBody()
        request_body.message = reply_message

        # 5. Execute the reply
        # Graph API POST /users/{id}/messages/{messageId}/reply
        await self.client.users.by_user_id(user_email)\
            .messages.by_message_id(parent_message_id)\
            .reply.post(body=request_body)

        logger.info(f"✅ Successfully replied to message {parent_message_id[:10]}...")
        return True

    
    def shutdown(self, timeout: float = 5.0):
        """Gracefully shutdown the worker thread"""
        if not self.worker_thread or not self.worker_thread.is_alive():
            return
        
        logger.info("🛑 Initiating AsyncioWorker shutdown")
        
        shutdown_queue = queue.Queue()
        self.request_queue.put(GraphRequest(
            request_type=RequestType.SHUTDOWN,
            params={},
            response_queue=shutdown_queue
        ))
        
        self._shutdown.set()
        self.worker_thread.join(timeout=timeout)
        
        if self.worker_thread.is_alive():
            logger.warning("⚠️  AsyncioWorker did not shutdown within timeout")
        else:
            logger.info("✅ AsyncioWorker shutdown complete")


class GraphConnector:
    """
    Thread-safe Graph API connector with resilient retry logic.
    
    Public interface is synchronous - all async complexity is hidden.
    Safe to use from any thread (Flask, SocketIO, main thread, etc.)
    
    Features:
    - Automatic retry with exponential backoff
    - Graceful error handling
    - Enhanced logging
    """
    
    # Bilal Khan (12/08/2026) - P1 Fix #5: Circuit-breaker constants
    # Trip after this many consecutive auth failures across any operation
    CIRCUIT_TRIP_THRESHOLD: int = 3
    # Stay open for this many seconds before auto-resetting
    CIRCUIT_COOLDOWN_SECONDS: float = 300.0  # 5 minutes

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        tenant_id: str,
        retry_config: Optional[RetryConfig] = None
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id
        self.retry_config = retry_config or RetryConfig()
        self.worker: Optional[AsyncioWorker] = None
        self._authenticated = False
        # Bilal Khan (12/08/2026) - P1 Fix #5: Circuit-breaker state
        self._consecutive_auth_failures: int = 0
        self._circuit_open_until: float = 0.0  # epoch seconds
    
    def authenticate(self) -> bool:
        """
        Initialize and start the asyncio worker thread.
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            self.worker = AsyncioWorker(
                self.client_id,
                self.client_secret,
                self.tenant_id,
                self.retry_config
            )
            self.worker.start()
            self._authenticated = True
            logger.info("✅ GraphConnector authenticated and worker started")
            return True
        
        except Exception as e:
            logger.error(f"❌ Authentication failed: {e}")
            self._authenticated = False
            return False
    
    def _send_request(self, request_type: RequestType, params: dict, timeout: float = 150.0) -> GraphResponse:  # Bilal Khan (05/08/2026) - Wave 1A: 60→150s so inner async timeout (120s) always fires first
        """
        Send a request to the asyncio worker and wait for response.
        
        Args:
            request_type: Type of request
            params: Request parameters
            timeout: Maximum time to wait for response (seconds)
            
        Returns:
            GraphResponse object
            
        Raises:
            RuntimeError: If not authenticated or timeout occurs
        """
        if not self._authenticated or not self.worker:
            raise RuntimeError("Not authenticated. Call authenticate() first.")
        
        response_queue = queue.Queue()
        
        request = GraphRequest(
            request_type=request_type,
            params=params,
            response_queue=response_queue
        )
        self.worker.request_queue.put(request)
        
        try:
            response = response_queue.get(timeout=timeout)
            return response
        except queue.Empty:
            # Bilal Khan (05/08/2026) - Wave 1A: Removed re-authenticate-on-timeout.
            # Previously: shutdown() + authenticate() spawned a new thread while the old one
            # was still alive (5s join not enough), causing a thread accumulation leak that
            # exhausted RAM under sustained Graph API throttling. Now we log and raise only.
            # The inner asyncio.wait_for (120s) always fires before this outer timeout (150s),
            # so the worker thread always puts a response on the queue before we get here.
            thread_alive = self.worker.worker_thread.is_alive() if self.worker else 'N/A'
            logger.error(
                f"❌ Graph request timed out after {timeout}s (outer queue). "
                f"Worker thread alive: {thread_alive}. This should not happen with aligned timeouts."
            )
            raise RuntimeError(f"Request timed out after {timeout} seconds")
    
    # Bilal Khan (12/08/2026) - P1 Fix #5: Circuit-breaker helpers - start
    def _is_circuit_open(self) -> bool:
        """Returns True if the circuit is currently open (tripped)."""
        if self._circuit_open_until > 0:
            remaining = self._circuit_open_until - time.time()
            if remaining > 0:
                logger.warning(
                    f"⚡ Circuit OPEN — Azure auth failure threshold hit. "
                    f"Skipping Graph API call. Resets in {remaining:.0f}s. "
                    f"Rotate the Azure client secret in .env and redeploy."
                )
                return True
            else:
                # Auto-reset after cooldown
                logger.info("⚡ Circuit RESET — cooldown elapsed, allowing Graph API calls again.")
                self._circuit_open_until = 0.0
                self._consecutive_auth_failures = 0
        return False

    def _record_outcome(self, success: bool, error: str = "") -> None:
        """Track consecutive auth failures and trip/reset the circuit."""
        if not success and _is_auth_error(Exception(error or "")):
            self._consecutive_auth_failures += 1
            logger.warning(
                f"🔐 Auth failure #{self._consecutive_auth_failures} / "
                f"{self.CIRCUIT_TRIP_THRESHOLD} before circuit trips."
            )
            if self._consecutive_auth_failures >= self.CIRCUIT_TRIP_THRESHOLD:
                self._circuit_open_until = time.time() + self.CIRCUIT_COOLDOWN_SECONDS
                logger.critical(
                    f"🚨 CIRCUIT TRIPPED — {self._consecutive_auth_failures} consecutive Azure "
                    f"authentication failures. All Graph API calls blocked for "
                    f"{self.CIRCUIT_COOLDOWN_SECONDS:.0f}s. "
                    f"ACTION REQUIRED: rotate AZURE_CLIENT_SECRET in .env and redeploy."
                )
        elif success:
            if self._consecutive_auth_failures > 0:
                logger.info(
                    f"✅ Circuit RESET — successful call after "
                    f"{self._consecutive_auth_failures} prior auth failure(s)."
                )
            self._consecutive_auth_failures = 0
            self._circuit_open_until = 0.0
    # Bilal Khan (12/08/2026) - P1 Fix #5: Circuit-breaker helpers - end

    def fetch_latest_emails(self, user_email: str, minutes: int = 10, top: int = 100) -> List[Dict]:
        """
        Fetch emails from the last N minutes (SYNCHRONOUS with retry).

        Returns empty list immediately if the auth circuit is open.

        Args:
            user_email: Email address to fetch from
            minutes: Time window in minutes
            top: Maximum number of emails to fetch

        Returns:
            List of email dicts
        """
        # Bilal Khan (12/08/2026) - P1 Fix #5: Check circuit before issuing request
        if self._is_circuit_open():
            return []
        try:
            response = self._send_request(
                RequestType.FETCH_EMAILS,
                {
                    'user_email': user_email,
                    'minutes': minutes,
                    'top': top
                }
            )

            self._record_outcome(response.success, response.error or "")
            if response.success:
                return response.data
            else:
                logger.error(f"❌ Fetch emails failed: {response.error}")
                return []

        except Exception as e:
            self._record_outcome(False, str(e))
            logger.error(f"❌ Error fetching emails: {e}")
            return []
    
    def get_attachment_sync(self, user_email: str, message_id: str, attachment_id: str):
        """
        Download attachment bytes (SYNCHRONOUS with retry).

        Returns None immediately if the auth circuit is open.

        Args:
            user_email: Email address
            message_id: Message ID
            attachment_id: Attachment ID

        Returns:
            bytes: Attachment data, or None if failed
        """
        # Bilal Khan (12/08/2026) - P1 Fix #5: Check circuit before issuing request
        if self._is_circuit_open():
            return None
        try:
            response = self._send_request(
                RequestType.GET_ATTACHMENT,
                {
                    'user_email': user_email,
                    'message_id': message_id,
                    'attachment_id': attachment_id
                },
                timeout=30.0
            )

            self._record_outcome(response.success, response.error or "")
            if response.success:
                return response.data
            else:
                logger.error(f"❌ Get attachment failed: {response.error}")
                return None

        except Exception as e:
            self._record_outcome(False, str(e))
            logger.error(f"❌ Error getting attachment: {e}")
            return None

    def fetch_all_emails(
        self,
        user_email: str,
        max_emails: int = 1000,
        skip: int = 0,
        checkpoint_callback: Optional[Callable] = None
    ):
        """
        Fetch all emails with pagination and checkpointing.
        
        This method is used by ingest.py for bulk email fetching.
        
        Args:
            user_email: Email address to fetch from
            max_emails: Maximum number of emails to fetch
            skip: Number of emails to skip (for resumption)
            checkpoint_callback: Optional callback(batch) called after each batch
        """
        try:
            batch_size = 100
            total_fetched = 0
            
            while total_fetched < max_emails:
                batch = self.fetch_latest_emails(
                    user_email,
                    minutes=365 * 24 * 60,  # 1 year
                    top=min(batch_size, max_emails - total_fetched)
                )
                
                if not batch:
                    logger.info("ℹ️  No more emails to fetch")
                    break
                
                total_fetched += len(batch)
                
                if checkpoint_callback:
                    checkpoint_callback(batch)
                
                logger.info(f"📊 Fetched {total_fetched}/{max_emails} emails...")
                
                if len(batch) < batch_size:
                    break
            
            logger.info(f"✅ Fetch complete: {total_fetched} emails")
        
        except Exception as e:
            logger.error(f"❌ Error in fetch_all_emails: {e}")
    
    def send_email(
        self,
        user_email: str,
        recipient: str,
        subject: str,
        body: str,
        conversation_id: str = None,
        cc: str = "",
        bcc: str = "",
        attachments: list = None
    ) -> bool:
        try:
            response = self._send_request(
                RequestType.SEND_EMAIL,
                {
                    'user_email': user_email,
                    'to_email': recipient,
                    'subject': subject,
                    'body_html': body,
                    'conversation_id': conversation_id,
                    'cc': cc,
                    'bcc': bcc,
                    'attachments': attachments
                }
            )
            return response.success
        except Exception as e:
            logger.error(f"❌ send_email failed: {e}")
            return False

    def reply_to_email(
        self,
        user_email: str,
        parent_message_id: str,
        body: str,
        to_email: str = None,
        cc: str = "",
        bcc: str = "",
        attachments: list = None
    ) -> bool:
        """
        Reply to an existing email (SYNCHRONOUS with retry).
        """
        try:
            response = self._send_request(
                RequestType.REPLY_TO_EMAIL,
                {
                    'user_email': user_email,
                    'parent_message_id': parent_message_id,
                    'body_html': body,
                    'to_email': to_email,
                    'cc': cc,
                    'bcc': bcc,
                    'attachments': attachments
                }
            )
            return response.success
        except Exception as e:
            logger.error(f"❌ reply_to_email failed: {e}")
            return False
    
    def shutdown(self):
        """Shutdown the asyncio worker thread cleanly"""
        if self.worker:
            self.worker.shutdown()
            self.worker = None
        self._authenticated = False
        logger.info("✅ GraphConnector shutdown complete")

    