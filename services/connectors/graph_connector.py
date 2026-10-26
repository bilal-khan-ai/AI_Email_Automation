"""
services/connectors/graph_connector.py
Streamlined, native synchronous Microsoft Graph API connector.
Uses MSAL for automatic token caching and requests.Session for pooled HTTP execution.
Zero OS worker threads, zero asyncio event loops, zero queues.
"""

import os
import io
import re
import time
import base64
import logging
import mimetypes
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from typing import List, Dict, Optional, Any, Callable

import msal
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
CHUNK_SIZE = 320 * 1024  # 320 KiB required by Microsoft Graph upload session


class RetryConfig:
    """Backward-compatible configuration shim for callers importing RetryConfig."""
    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        exponential_base: float = 2.0,
        jitter: bool = True
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter


class ParentMessageNotFoundError(Exception):
    """Raised when target parent message does not exist or was moved in Microsoft Graph."""
    pass


class GraphConnector:
    """
    Thread-safe, synchronous Microsoft Graph connector with connection pooling,
    automatic token caching, large attachment upload session support, and
    circuit-breaker protection.
    """

    CIRCUIT_TRIP_THRESHOLD: int = 3
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

        self._authenticated = False
        self._consecutive_auth_failures: int = 0
        self._circuit_open_until: float = 0.0
        self._msal_client = None
        self._scopes = ["https://graph.microsoft.com/.default"]

        # Reusable HTTP Session with connection pooling and transient backoff
        self.session = requests.Session()
        retries = Retry(
            total=self.retry_config.max_retries,
            backoff_factor=self.retry_config.base_delay,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PATCH", "PUT"]
        )
        adapter = HTTPAdapter(pool_connections=5, pool_maxsize=15, max_retries=retries)
        self.session.mount("https://", adapter)

    @property
    def _msal_app(self):
        """Lazily initialize MSAL Confidential Client."""
        if self._msal_client is None:
            self._msal_client = msal.ConfidentialClientApplication(
                client_id=self.client_id,
                client_credential=self.client_secret,
                authority=f"https://login.microsoftonline.com/{self.tenant_id}"
            )
        return self._msal_client

    def _is_circuit_open(self) -> bool:
        """Checks if auth circuit breaker is tripped."""
        if self._circuit_open_until > 0:
            remaining = self._circuit_open_until - time.time()
            if remaining > 0:
                logger.warning(
                    f"Circuit OPEN — Azure auth failure threshold hit. "
                    f"Skipping Graph API call. Resets in {remaining:.0f}s."
                )
                return True
            else:
                logger.info("Circuit RESET — cooldown elapsed, allowing Graph API calls again.")
                self._circuit_open_until = 0.0
                self._consecutive_auth_failures = 0
        return False

    def _record_outcome(self, success: bool, is_auth_error: bool = False) -> None:
        """Tracks auth outcomes to trip or reset the circuit breaker."""
        if not success and is_auth_error:
            self._consecutive_auth_failures += 1
            logger.warning(
                f"Auth failure #{self._consecutive_auth_failures} / "
                f"{self.CIRCUIT_TRIP_THRESHOLD} before circuit trips."
            )
            if self._consecutive_auth_failures >= self.CIRCUIT_TRIP_THRESHOLD:
                self._circuit_open_until = time.time() + self.CIRCUIT_COOLDOWN_SECONDS
                logger.critical(
                    f"CIRCUIT TRIPPED — {self._consecutive_auth_failures} consecutive Azure auth failures. "
                    f"Calls blocked for {self.CIRCUIT_COOLDOWN_SECONDS:.0f}s."
                )
        elif success:
            if self._consecutive_auth_failures > 0:
                logger.info("Circuit RESET after successful call.")
            self._consecutive_auth_failures = 0
            self._circuit_open_until = 0.0

    def _get_auth_headers(self) -> Dict[str, str]:
        """Returns valid Bearer authorization header with cached token."""
        result = self._msal_app.acquire_token_silent(self._scopes, account=None)
        if not result:
            result = self._msal_app.acquire_token_for_client(scopes=self._scopes)

        if "access_token" not in result:
            error_msg = result.get("error_description", result.get("error", "Unknown auth failure"))
            self._record_outcome(False, is_auth_error=True)
            raise PermissionError(f"Azure AD authentication failed: {error_msg}")

        self._record_outcome(True)
        return {
            "Authorization": f"Bearer {result['access_token']}",
            "Content-Type": "application/json"
        }

    def authenticate(self) -> bool:
        """Validates credentials on startup."""
        try:
            self._get_auth_headers()
            self._authenticated = True
            logger.info("GraphConnector authenticated successfully")
            return True
        except Exception as e:
            logger.error(f"GraphConnector authentication failed: {e}")
            self._authenticated = False
            return False

    def _parse_recipient(self, raw_input: str) -> Optional[Dict[str, Any]]:
        """Parses RFC-5322 string into Graph Recipient object."""
        if not raw_input or not raw_input.strip():
            return None
        name, addr = parseaddr(raw_input.strip())
        clean_addr = addr or name
        if not clean_addr or "@" not in clean_addr:
            return None
        return {
            "emailAddress": {
                "address": clean_addr.strip(),
                "name": name.strip() or clean_addr.strip()
            }
        }

    def _parse_recipients_list(self, raw_str: str) -> List[Dict[str, Any]]:
        """Splits comma/semicolon delimited string into list of Graph Recipient dicts."""
        if not raw_str:
            return []
        items = raw_str.replace(";", ",").split(",")
        recipients = []
        for item in items:
            parsed = self._parse_recipient(item)
            if parsed:
                recipients.append(parsed)
        return recipients

    def _format_attachment(self, att: Dict[str, Any]) -> Dict[str, Any]:
        """Normalizes and prepares attachment dictionary for Graph API."""
        raw_payload = att.get('content') or ''
        if isinstance(raw_payload, str):
            clean_b64 = re.sub(r'^data:[^;]+;base64,', '', raw_payload).strip()
        elif isinstance(raw_payload, bytes):
            clean_b64 = base64.b64encode(raw_payload).decode('utf-8')
        else:
            clean_b64 = ''

        cid = att.get('content_id') or att.get('cid') or att.get('contentId')
        is_inline = bool(att.get('is_inline') or att.get('isInline') or cid)
        content_type = att.get('contentType') or att.get('content_type') or 'application/octet-stream'

        attachment_dict: Dict[str, Any] = {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": att.get('name', 'attachment'),
            "contentType": content_type,
            "contentBytes": clean_b64,
            "isInline": is_inline
        }
        if cid:
            attachment_dict["contentId"] = cid

        return attachment_dict

    def _parse_email(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """Transforms Graph REST API JSON message into standardized dictionary."""
        attachments = []
        raw_attachments = msg.get('attachments') or []
        for att in raw_attachments:
            if att.get('@odata.type') == '#microsoft.graph.itemAttachment':
                continue
            attachments.append({
                'id': att.get('id', ''),
                'name': att.get('name', 'unknown'),
                'content_type': att.get('contentType', ''),
                'size': att.get('size', 0),
                'is_inline': att.get('isInline', False),
                'content_id': att.get('contentId', None)
            })

        sender_email = "unknown"
        from_dict = msg.get('from', {}) or {}
        if from_dict.get('emailAddress'):
            sender_email = from_dict['emailAddress'].get('address') or from_dict['emailAddress'].get('name', 'unknown')
        elif msg.get('sender', {}).get('emailAddress'):
            sender_email = msg['sender']['emailAddress'].get('address') or msg['sender']['emailAddress'].get('name', 'unknown')

        unique_body = msg.get('uniqueBody', {}) or {}
        full_body = msg.get('body', {}) or {}
        primary_html = unique_body.get('content') or full_body.get('content') or ''
        body_preview = msg.get('bodyPreview') or primary_html[:255]

        to_recipients = [r.get('emailAddress', {}).get('address', '') for r in msg.get('toRecipients', []) if r.get('emailAddress', {}).get('address')]
        cc_recipients = [r.get('emailAddress', {}).get('address', '') for r in msg.get('ccRecipients', []) if r.get('emailAddress', {}).get('address')]
        bcc_recipients = [r.get('emailAddress', {}).get('address', '') for r in msg.get('bccRecipients', []) if r.get('emailAddress', {}).get('address')]

        received_dt = msg.get('receivedDateTime')
        if not received_dt:
            received_dt = datetime.now(timezone.utc).isoformat()

        return {
            'id': msg.get('id'),
            'internet_message_id': msg.get('internetMessageId'),
            'conversation_id': msg.get('conversationId', msg.get('id')),
            'subject': msg.get('subject', 'No Subject'),
            'body_html': primary_html,
            'body_text': body_preview,
            'body': primary_html,
            'sender': sender_email,
            'received': received_dt,
            'attachments': attachments,
            'to': ', '.join(to_recipients),
            'cc': ', '.join(cc_recipients),
            'bcc': ', '.join(bcc_recipients)
        }

    # -------------------------------------------------------------------------
    # 1. FETCH EMAILS
    # -------------------------------------------------------------------------
    def fetch_latest_emails(
        self,
        user_email: str,
        minutes: int = 10,
        top: int = 100,
        folder: Optional[str] = None
    ) -> List[Dict]:
        """Fetch emails from the last N minutes synchronously (default: all mailbox messages)."""
        if self._is_circuit_open():
            return []

        try:
            threshold = (datetime.utcnow() - timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%SZ')
            params = {
                "$filter": f"isDraft eq false and receivedDateTime ge {threshold}",
                "$select": "id,conversationId,subject,body,uniqueBody,bodyPreview,from,receivedDateTime,hasAttachments,ccRecipients,bccRecipients,toRecipients,internetMessageId,isDraft",
                "$orderby": "receivedDateTime ASC",
                "$top": str(top),
                "$expand": "attachments"
            }

            if folder:
                url = f"{GRAPH_BASE_URL}/users/{user_email}/mailFolders/{folder}/messages"
            else:
                url = f"{GRAPH_BASE_URL}/users/{user_email}/messages"
            headers = self._get_auth_headers()
            emails = []

            while url:
                resp = self.session.get(url, headers=headers, params=params if url.startswith(GRAPH_BASE_URL) and '?' not in url else None, timeout=30.0)
                if resp.status_code == 401:
                    self._record_outcome(False, is_auth_error=True)
                    return []
                resp.raise_for_status()
                data = resp.json()

                for msg in data.get('value', []):
                    if msg.get('isDraft'):
                        continue
                    emails.append(self._parse_email(msg))

                # Handle nextLink pagination
                url = data.get('@odata.nextLink')
                params = None  # Next link already contains query parameters

            logger.info(f"Fetched {len(emails)} emails for {user_email} (last {minutes} minutes)")
            return emails

        except Exception as e:
            logger.error(f"Error fetching emails for {user_email}: {e}")
            return []

    # -------------------------------------------------------------------------
    # 2. GET ATTACHMENT
    # -------------------------------------------------------------------------
    def get_attachment_sync(self, user_email: str, message_id: str, attachment_id: str) -> Optional[bytes]:
        """Download attachment bytes synchronously."""
        if self._is_circuit_open():
            return None

        try:
            url = f"{GRAPH_BASE_URL}/users/{user_email}/messages/{message_id}/attachments/{attachment_id}"
            headers = self._get_auth_headers()
            resp = self.session.get(url, headers=headers, timeout=30.0)
            
            if resp.status_code == 404:
                logger.warning(f"Attachment {attachment_id[:15]} not found for message {message_id[:15]}")
                return None
            resp.raise_for_status()

            data = resp.json()
            content_b64 = data.get('contentBytes')
            if content_b64:
                return base64.b64decode(content_b64)
            return None

        except Exception as e:
            logger.error(f"Error downloading attachment {attachment_id[:15]}: {e}")
            return None

    # -------------------------------------------------------------------------
    # 3. REPLY TO EMAIL (Single-part & Upload Session Chunking)
    # -------------------------------------------------------------------------
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
        Reply to an existing email with automatic payload size thresholding,
        chunked upload session support for files >= 3 MB, and fallback routing.
        """
        if self._is_circuit_open():
            return False

        if not parent_message_id:
            logger.warning("No parent message ID provided. Raising ParentMessageNotFoundError.")
            raise ParentMessageNotFoundError("No parent message ID provided.")

        attachments = attachments or []
        formatted_attachments = [self._format_attachment(a) for a in attachments]

        # Calculate total payload size to decide between single-shot and upload session
        total_size = sum(len(a.get('contentBytes', '')) for a in formatted_attachments)
        has_large_file = any(len(a.get('contentBytes', '')) >= 3 * 1024 * 1024 for a in formatted_attachments)

        try:
            if not has_large_file and total_size < 3.5 * 1024 * 1024:
                # FAST PATH: Standard single-shot reply
                return self._send_standard_reply(
                    user_email=user_email,
                    parent_message_id=parent_message_id,
                    body=body,
                    to_email=to_email,
                    cc=cc,
                    bcc=bcc,
                    formatted_attachments=formatted_attachments
                )
            else:
                # CHUNKED PATH: Draft creation + Upload Session
                logger.info(f"Payload size ({total_size / (1024*1024):.2f} MB) requires Graph Upload Session")
                return self._send_draft_chunked_reply(
                    user_email=user_email,
                    parent_message_id=parent_message_id,
                    body=body,
                    to_email=to_email,
                    cc=cc,
                    bcc=bcc,
                    formatted_attachments=formatted_attachments
                )

        except requests.HTTPError as http_err:
            if http_err.response is not None and http_err.response.status_code == 404:
                logger.warning(
                    f"Parent message {parent_message_id[:10]} not found in Graph API (404). "
                    "Raising ParentMessageNotFoundError for user confirmation."
                )
                raise ParentMessageNotFoundError(
                    f"Parent message {parent_message_id} was not found in Microsoft Graph (404)."
                )
            logger.error(f"HTTP error during reply: {http_err}")
            return False
        except Exception as e:
            if isinstance(e, ParentMessageNotFoundError):
                raise
            logger.error(f"Unexpected error during reply: {e}")
            return False

    def _send_standard_reply(
        self,
        user_email: str,
        parent_message_id: str,
        body: str,
        to_email: str,
        cc: str,
        bcc: str,
        formatted_attachments: list
    ) -> bool:
        """Single-shot POST /messages/{id}/reply."""
        url = f"{GRAPH_BASE_URL}/users/{user_email}/messages/{parent_message_id}/reply"
        headers = self._get_auth_headers()

        reply_message: Dict[str, Any] = {}
        if to_email:
            parsed_to = self._parse_recipient(to_email)
            if parsed_to:
                reply_message['toRecipients'] = [parsed_to]
        if cc:
            reply_message['ccRecipients'] = self._parse_recipients_list(cc)
        if bcc:
            reply_message['bccRecipients'] = self._parse_recipients_list(bcc)

        if formatted_attachments:
            reply_message['attachments'] = formatted_attachments

        payload = {
            "comment": body,
            "message": reply_message
        }

        resp = self.session.post(url, headers=headers, json=payload, timeout=45.0)
        resp.raise_for_status()

        if resp.status_code in (200, 202):
            logger.info(f"Successfully replied to message {parent_message_id[:10]} via standard endpoint")
            return True
        return False

    def _send_draft_chunked_reply(
        self,
        user_email: str,
        parent_message_id: str,
        body: str,
        to_email: str,
        cc: str,
        bcc: str,
        formatted_attachments: list
    ) -> bool:
        """Creates a draft reply, uploads large files via createUploadSession, and sends draft."""
        headers = self._get_auth_headers()

        # Step 1: Create draft reply
        create_draft_url = f"{GRAPH_BASE_URL}/users/{user_email}/messages/{parent_message_id}/createReply"
        resp = self.session.post(create_draft_url, headers=headers, json={}, timeout=30.0)
        resp.raise_for_status()
        draft_msg = resp.json()
        draft_id = draft_msg['id']

        # Step 2: Update draft body and recipient overrides
        update_draft_url = f"{GRAPH_BASE_URL}/users/{user_email}/messages/{draft_id}"
        patch_payload: Dict[str, Any] = {
            "body": {
                "contentType": "HTML",
                "content": body
            }
        }
        if to_email:
            parsed_to = self._parse_recipient(to_email)
            if parsed_to:
                patch_payload['toRecipients'] = [parsed_to]
        if cc:
            patch_payload['ccRecipients'] = self._parse_recipients_list(cc)
        if bcc:
            patch_payload['bccRecipients'] = self._parse_recipients_list(bcc)

        patch_resp = self.session.patch(update_draft_url, headers=headers, json=patch_payload, timeout=30.0)
        patch_resp.raise_for_status()

        # Step 3: Attach files
        for att in formatted_attachments:
            raw_bytes = base64.b64decode(att.get('contentBytes', ''))
            file_size = len(raw_bytes)

            if file_size < 3 * 1024 * 1024:
                # Direct attachment for small files
                add_att_url = f"{GRAPH_BASE_URL}/users/{user_email}/messages/{draft_id}/attachments"
                att_resp = self.session.post(add_att_url, headers=headers, json=att, timeout=30.0)
                att_resp.raise_for_status()
            else:
                # Large file upload session
                session_url = f"{GRAPH_BASE_URL}/users/{user_email}/messages/{draft_id}/attachments/createUploadSession"
                session_payload = {
                    "AttachmentItem": {
                        "attachmentType": "file",
                        "name": att.get('name', 'attachment'),
                        "size": file_size,
                        "contentType": att.get('contentType', 'application/octet-stream')
                    }
                }
                sess_resp = self.session.post(session_url, headers=headers, json=session_payload, timeout=30.0)
                sess_resp.raise_for_status()
                upload_url = sess_resp.json().get('uploadUrl')

                # Stream chunks of 320 KiB
                bytes_stream = io.BytesIO(raw_bytes)
                start = 0
                while start < file_size:
                    chunk = bytes_stream.read(CHUNK_SIZE)
                    chunk_len = len(chunk)
                    end = start + chunk_len - 1
                    chunk_headers = {
                        "Content-Length": str(chunk_len),
                        "Content-Range": f"bytes {start}-{end}/{file_size}"
                    }
                    put_resp = self.session.put(upload_url, headers=chunk_headers, data=chunk, timeout=60.0)
                    put_resp.raise_for_status()
                    start += chunk_len

        # Step 4: Send the draft
        send_url = f"{GRAPH_BASE_URL}/users/{user_email}/messages/{draft_id}/send"
        send_resp = self.session.post(send_url, headers=headers, json={}, timeout=45.0)
        send_resp.raise_for_status()

        logger.info(f"Successfully sent chunked reply for parent message {parent_message_id[:10]}")
        return True

    # -------------------------------------------------------------------------
    # 4. SEND EMAIL (POST /users/{id}/sendMail)
    # -------------------------------------------------------------------------
    def send_email(
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
        """Standalone email transmission endpoint."""
        if self._is_circuit_open():
            return False

        try:
            url = f"{GRAPH_BASE_URL}/users/{user_email}/sendMail"
            headers = self._get_auth_headers()

            message: Dict[str, Any] = {
                "subject": subject,
                "body": {
                    "contentType": "HTML",
                    "content": body_html
                }
            }

            if conversation_id:
                message["conversationId"] = conversation_id

            if to_email:
                parsed_to = self._parse_recipient(to_email)
                if parsed_to:
                    message["toRecipients"] = [parsed_to]
            if cc:
                message["ccRecipients"] = self._parse_recipients_list(cc)
            if bcc:
                message["bccRecipients"] = self._parse_recipients_list(bcc)

            if attachments:
                message["attachments"] = [self._format_attachment(a) for a in attachments]

            payload = {
                "message": message,
                "saveToSentItems": True
            }

            resp = self.session.post(url, headers=headers, json=payload, timeout=45.0)
            resp.raise_for_status()
            logger.info(f"Successfully sent email to {to_email}")
            return True

        except Exception as e:
            logger.error(f"Error sending email to {to_email}: {e}")
            return False

    # -------------------------------------------------------------------------
    # 5. FETCH ALL EMAILS (Pagination Helper for Ingest)
    # -------------------------------------------------------------------------
    def fetch_all_emails(
        self,
        user_email: str,
        max_emails: int = 1000,
        skip: int = 0,
        checkpoint_callback: Optional[Callable] = None
    ):
        """Fetch all emails with pagination and checkpoint callbacks."""
        try:
            batch_size = 100
            total_fetched = 0

            while total_fetched < max_emails:
                batch = self.fetch_latest_emails(
                    user_email=user_email,
                    minutes=365 * 24 * 60,
                    top=min(batch_size, max_emails - total_fetched)
                )

                if not batch:
                    logger.info("No more emails to fetch")
                    break

                total_fetched += len(batch)
                if checkpoint_callback:
                    checkpoint_callback(batch)

                logger.info(f"Fetched {total_fetched}/{max_emails} emails...")
                if len(batch) < batch_size:
                    break

            logger.info(f"Fetch complete: {total_fetched} emails")

        except Exception as e:
            logger.error(f"Error in fetch_all_emails: {e}")

    def shutdown(self):
        """Closes pooled HTTP sessions cleanly."""
        self.session.close()
        self._authenticated = False
        logger.info("GraphConnector session closed")


__all__ = ['GraphConnector', 'RetryConfig', 'ParentMessageNotFoundError']