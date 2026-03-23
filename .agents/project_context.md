# AI Email Automation Project Context

This document provides a quick reference for the project's file structure, core features, and code locations.

---

## Root Directory

### [main.py](file:///c:/Users/Bilal/OneDrive%20-%20Greenware%20Solutions%20LLP/AI_Email_Automation/main.py)
Core application entry point and lifecycle manager for the Support Agent.

- **SupportAgent Initialization**: Orchestrates all modules (SQL, Graph, Vector DBs, AI, Processors). `[Line 86]`
- **Signal Handling**: Graceful shutdown on SIGINT/SIGTERM. `[Lines 45-66]`
- **Ticket Cleanup Daemon**: Background thread for soft and hard deletion of tickets. `[Lines 195, 763]`
- **Email Ingestion (`ingest_email`)**: Handles PII redaction, attachment extraction, and ticket linking. `[Line 234]`
- **AI Response Generation (`generate_ai_response`)**: Dual RAG search and GPT-4 response drafting. `[Line 458]`
- **Live Polling Loop (`run_live`)**: Periodic fetching of new emails from MS Graph. `[Line 866]`
- **Backlog Processing (`run_backlog`)**: One-time scan of historical emails. `[Line 830]`

### [auth.py](file:///c:/Users/Bilal/OneDrive%20-%20Greenware%20Solutions%20LLP/AI_Email_Automation/auth.py)
User authentication, authorization, and session management.

- **UserManager Class**: Database-backed user management with PostgreSQL. `[Line 42]`
- **Password Hashing**: Secure storage using SHA-256 and unique salts. `[Line 101, 142]`
- **Role-Based Access Control (RBAC)**: Supports 'admin' and 'user' roles. `[Line 62]`
- **Login Required Decorator**: Route protection for authenticated sessions. `[Line 534]`
- **Admin Required Decorator**: Restricted access for administrative tasks. `[Line 552]`
- **Initial Admin Creation**: Automatic creation of default 'admin' user if none exists. `[Line 156]`

### [dashboard_socketio.py](file:///c:/Users/Bilal/OneDrive%20-%20Greenware%20Solutions%20LLP/AI_Email_Automation/dashboard_socketio.py)
Real-time WebSocket events for the multi-user dashboard.

- **SocketIO Initialization**: Integrated with Flask for bi-directional communication. `[Line 47]`
- **Ticket Locking**: Prevents concurrent edits by different users. `[Line 156]`
- **Live Stats Calculation**: Real-time aggregation of open, closed, and total ticket counts. `[Line 96]`
- **Manual Email Responses**: Logic for sending emails directly from the dashboard. `[Line 245]`
- **Ticket Status/Assignment Updates**: Instant broadcasting of ticket changes to all clients. `[Lines 340, 520]`
- **Attachment Download Handling**: Secure retrieval of message attachments for users. `[Line 620]`

### [config.py](file:///c:/Users/Bilal/OneDrive%20-%20Greenware%20Solutions%20LLP/AI_Email_Automation/config.py)
Centralized configuration manager utilizing environment variables.

- **Credential Management**: Azure, OpenAI, and Database connection strings. `[Lines 8-31]`
- **Polling & Cleanup Intervals**: Configurable timers for system tasks. `[Lines 34, 47]`
- **Feature Flags**: Toggles for auto-responses and test mode. `[Lines 36, 41]`

---

## Modules Directory (`/modules`)

### [sql_logger.py](file:///c:/Users/Bilal/OneDrive%20-%20Greenware%20Solutions%20LLP/AI_Email_Automation/modules/sql_logger.py)
PostgreSQL backend for ticket persistence and logging.

- **Schema Initialization**: Automated setup of `tickets` and `messages` tables with indices. `[Line 105]`
- **Soft-Delete Implementation**: Tracking logic for temporary vs permanent deletion. `[Lines 135, 780]`
- **Ticket Retrieval**: Optimized queries for active tickets and full message threads. `[Lines 320, 395]`
- **Metadata Management**: Logging of AI entities, draft statuses, and lock states. `[Lines 150, 605]`

### [vector_db.py](file:///c:/Users/Bilal/OneDrive%20-%20Greenware%20Solutions%20LLP/AI_Email_Automation/modules/vector_db.py)
Semantic search and retrieval using ChromaDB.

- **VRAM Management**: Context-managed loading/unloading of embedding models to save GPU memory. `[Lines 26, 92]`
- **Soft-Delete Mechanism**: Parallel `_deleted` collection to filter search results. `[Lines 135, 345]`
- **Batch Processing**: Efficient ingestion of multiple emails with progress tracking. `[Line 211]`
- **BookVectorDB Class**: Specialized instance for authoritative technical documentation. `[Line 511]`

### [openai_agent.py](file:///c:/Users/Bilal/OneDrive%20-%20Greenware%20Solutions%20LLP/AI_Email_Automation/modules/openai_agent.py)
AI orchestration for response generation and data analysis.

- **Enhanced RAG Strategy**: Integration of documentation, past experiences, and thread history. `[Line 85]`
- **Reasoning Framework**: System prompts enforcing documentation priority and sendability checks. `[Lines 140, 480]`
- **Summarization & Sentiment**: AI-driven analysis of message content and intent. `[Lines 315, 355]`
- **Token Management**: Context window optimization and summary-fallback for long threads. `[Line 230]`

### [graph_connector.py](file:///c:/Users/Bilal/OneDrive%20-%20Greenware%20Solutions%20LLP/AI_Email_Automation/modules/graph_connector.py)
Resilient Microsoft Graph API integration.

- **Asyncio Worker Thread**: Handles async Graph calls synchronously for thread safety. `[Line 164]`
- **Exponential Backoff**: Automatic retry logic with jitter for API resilience. `[Lines 58, 109]`
- **Email/Attachment Ingestion**: Robust methods for fetching and decoding MS Graph data. `[Lines 324, 364]`

### [doc_processor.py](file:///c:/Users/Bilal/OneDrive%20-%20Greenware%20Solutions%20LLP/AI_Email_Automation/modules/doc_processor.py)
Extraction logic for `.docx` and `.pdf` attachments.

- **Docx Parsing**: Text extraction and image retrieval from XML structure. `[Line 144]`
- **PDF Extraction**: Text per-page retrieval and OCR fallback for scanned pages. `[Lines 235, 287]`
- **Tables Integration**: Programmable detection of tabular data within documents. `[Line 324]`

### [image_processor.py](file:///c:/Users/Bilal/OneDrive%20-%20Greenware%20Solutions%20LLP/AI_Email_Automation/modules/image_processor.py)
Visual analysis and OCR for image attachments.

- **VRAM Cleanup Manager**: Aggressive GPU memory release after every inference. `[Line 38]`
- **BLIP Captioning**: Generates visual descriptions of screenshots and photos. `[Line 237]`
- **EasyOCR Integration**: CPU-bound text extraction from images. `[Lines 194, 243]`

### [tables_processor.py](file:///c:/Users/Bilal/OneDrive%20-%20Greenware%20Solutions%20LLP/AI_Email_Automation/modules/tables_processor.py)
Summarization of tabular files (`.xlsx`, `.csv`, `.tsv`).

- **Domain-Agnostic Parsing**: Converts raw data into high-signal summaries for LLM prompts. `[Line 313]`
- **Messy Header Recovery**: Heuristics to find correct headers in complex spreadsheets. `[Lines 115, 185]`
- **Anomaly Detection**: Statistics-based signals for missingness, outliers, and duplicates. `[Line 506]`

### [pii_redactor.py](file:///c:/Users/Bilal/OneDrive%20-%20Greenware%20Solutions%20LLP/AI_Email_Automation/modules/pii_redactor.py)
Data privacy protection using Microsoft Presidio.

- **Indian PII Recognizers**: Custom patterns for PAN, Aadhaar, and Bank Account numbers. `[Lines 39-90]`
- **Generic Redaction**: Email, Phone, IP Address, and Location scrubbing. `[Lines 121-143]`

---

## Utility Directory (`/utility`)

- **bookstack_ingest.py**: Bulk ingestion for technical documentation.
- **ingest.py**: Command-line tool for historical email ingestion.
- **kb_diagnostic.py**: Knowledge base verification and query testing tool.
- **cleanup_vectors.py**: Maintenance tool for vector database optimization.

---

## Templates Directory (`/templates`)

- **dashboard.html**: Premium, real-time ticket management interface.
- **admin_users.html**: Portal for managing staff access and roles.
- **login.html**: Secure entry point for the application.

---

## Management Tools

### [manage_users.py](file:///c:/Users/Bilal/OneDrive%20-%20Greenware%20Solutions%20LLP/AI_Email_Automation/manage_users.py)
CLI for administrative user operations (create, delete, list, password-reset). `[Line 41]`
