# AI Email Automation Project Context

This document provides a quick reference for the project's file structure, core features, and code locations.

---

## Root Directory

### [main.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/main.py)
Core application entry point and lifecycle manager for the Support Agent.

- **SupportAgent Initialization**: Orchestrates all modules (SQL, Graph, Vector DBs, AI, Processors). `[Line 154]`
- **Signal Handling**: Graceful shutdown on SIGINT/SIGTERM. `[Lines 235-242]`
- **Ticket Cleanup Daemon**: Background thread for automated ticket archival and removal. `[Lines 244-301]`
- **Ticket Lifecycle Management**:
    - **Soft Delete**: Hides inactive closed tickets from the UI after 1 day (Configurable). Automatically restored if a new email (customer or internal) is received for the ticket.
- **Hard Delete**: Permanently removes old records from PostgreSQL after 6 days (Configurable).
- **Knowledge Retention**: All message data remains in ChromaDB (Vector Store) indefinitely for RAG context, even after the source ticket is hard-deleted from SQL.
- **PII Redaction**: Automatic stripping of Phones, Emails, Names, and Locations before RAG ingestion using a dedicated `PIIRedactor` module. `[Line 429]`
- **Attachment Processing**: Extraction of text from Images (Vision), Tables (XLSX/CSV), and Docs (PDF/DOCX). Descriptions are stored as metadata in Vector DB.
- **Inline Image Baking**: Small inline images (signatures, screenshots <1MB) are baked into HTML as Base64 during ingestion to ensure reliable rendering. `[Line 494]`
- **Live Polling Loop (`run_live`)**: Periodic fetching of new emails from MS Graph. `[Line 798]`
- **Internal Ticket Creation**: Supports staff creating tickets by emailing the support address directly; capturing the sender as the internal `actor`.

### [auth.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/auth.py)
User authentication, authorization, and session management.

- **UserManager Class**: Database-backed user management with PostgreSQL. `[Line 42]`
- **Role-Based Access**: Supports `admin`, `staff`, and `user` roles.
- **Assignable User Flag**: `is_assignable` property allows Admins to exclude specific users (e.g., technical accounts) from the dashboard load balancer and assignment dropdowns.
- **Password Security**: SHA-256 with per-user salts.
    - **Staff**: Regular support access to assigned tickets.
- **Account Toggling**: Enable/Disable staff accounts without deleting history. `[Line 328]`
- **Login Required**: Route protection and role-specific redirection.

### [dashboard_socketio.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/dashboard_socketio.py)
Real-time WebSocket events for the multi-user dashboard.

- **Management Console (`/management`)**: Admin-only page for staff performance and user control.
- **Staff Performance Grid**: Real-time cards showing Assigned, Open, and Closed tickets.
- **Time Range Metrics**: Dropdown filtering (Today to All Time) powered by WebSockets. `[Line 715]`
- **Staff-Specific Dashboard**: Staff only see tickets and stats assigned to their username. `[Line 308]`
- **Ticket Locking**: Prevents concurrent edits by different users. `[Line 626]`
- **Live Ticket Reassignment**: Immediate, SocketIO-driven reassignment via the modal dropdown (auto-broadcasts to all users).
- **Manual Email Responses**: Logic for sending emails directly from the dashboard.
- **UI/UX Modernization**: High-fidelity Outlook-style rendering, sticky timeline header, and hardened search fields (autofill protection).

### [config.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/config.py)
Centralized configuration manager utilizing environment variables (`.env`).

- **Credential Management**: Azure, OpenAI, and PostgreSQL connection strings.
- **Feature Flags**: Toggles for auto-responses (`AUTO_GENERATE_RESPONSES`) and test mode (`TEST_MODE`).
- **Cleanup Settings**: Configurable intervals for the cleanup daemon.

---

## Modules Directory (`/modules`)

### [sql_logger.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/modules/sql_logger.py)
PostgreSQL backend for ticket persistence and logging.

- **Schema Initialization**: Automated setup of `tickets` and `messages` tables.
- **Event-Driven Auto-Assignment**: Assignment logic shifted from "Least Busy" load balancer at creation to **User-Action-Based Assignment**. Tickets are assigned to the user who "Takes" them, saves a draft, or sends a response.
- **Human-Readable Timeline**: Centralized event logging system providing a readable history (e.g., "aaaaa saved a draft.", "bbbb takes ticket."). `[Line 1324]`
- **Staff Metrics**: Aggregated performance data with time-range filtering support (PostgreSQL-optimized). `[Line 901]`
- **Soft-Delete Implementation**: Automated cleanup via `main.py` daemon thread. Thresholds: 1 day for Soft-Delete, 6 days for Hard-Delete (Configurable via `.env`).
- **Metadata Management**: Logging of AI entities, draft statuses, and lock states using JSONB columns for extensibility.

### [vector_db.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/modules/vector_db.py)
Semantic search and retrieval using ChromaDB with dynamic CPU/GPU handling.

- **Dynamic Device Selection**: Automatically detects CUDA GPU; falls back to CPU for text embeddings. `[Line 53]`
- **VRAM Management**: Context-managed loading/unloading of embedding models to ensure zero lingering VRAM usage. `[Line 26]`
- **Soft-Delete Mechanism**: Parallel `_deleted` collection to filter search results. `[Line 130]`
- **BookVectorDB**: Specialized instance for authoritative technical documentation. `[Line 505]`

### [openai_agent.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/modules/openai_agent.py)
AI orchestration for response generation, image analysis, and data categorization.

- **Cloud Vision**: analyze_image() uses `gpt-4o` or `gpt-4o-mini` for OCR and visual analysis. `[Line 617]`
- **Enhanced RAG Strategy**: Integration of documentation (authoritative) and experience (advisory). `[Line 153]`
- **Reasoning Framework**: System prompts enforcing documentation priority and sendability checks. `[Lines 235, 334]`
- **Model Standard**: Uses `gpt-4o-mini` for categorization and `gpt-5-mini` for main response generation.

### [graph_connector.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/modules/graph_connector.py)
Resilient Microsoft Graph API integration.

- **Exponential Backoff**: Automatic retry logic with jitter for API resilience.
- **Email/Attachment Ingestion**: Robust methods for fetching and decoding MS Graph data.

### [doc_processor.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/modules/doc_processor.py)
Extraction logic for `.docx` and `.pdf` attachments.

- **Docx Parsing**: Text extraction and embedded image retrieval. `[Line 144]`
- **PDF Extraction**: Text per-page retrieval and OCR fallback (via ImageProcessor). `[Lines 238, 291]`
- **Table Detection**: Heuristic detection of tabular data within PDF text. `[Line 328]`

### [image_processor.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/modules/image_processor.py)
Visual analysis and OCR for image attachments - **Fully Cloud Integrated**.

- **OpenAI Vision Offloading**: Removed local BLIP and EasyOCR; all vision tasks routed to OpenAI. `[Line 99]`
- **Noise Filtering**: Local keywords skip low-quality icons/logos to save tokens. `[Line 42]`
- **Normalization**: Lightweight CPU-based image preprocessing (resizing/RGB conversion). `[Line 120]`

### [tables_processor.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/modules/tables_processor.py)
Domain-agnostic summarization of tabular files (`.xlsx`, `.csv`, `.tsv`).

- **Compact Summaries**: Converts raw data into high-signal text for LLM prompts. `[Line 313]`
- **Anomaly Detection**: Stats-based signals for missingness, outliers, and duplicates. `[Line 506]`

### [pii_redactor.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/modules/pii_redactor.py)
Data privacy protection using Microsoft Presidio.

- **Indian PII Recognizers**: Custom patterns for PAN, Aadhaar, and Bank Account numbers.
- **Generic Redaction**: Email, Phone, IP Address, and Location scrubbing.

---

## Utility Directory (`/utility`)

- **ingest.py**: Command-line tool for historical email ingestion (Resilient & Cloud-Vision ready).
- **bookstack_ingest.py**: Bulk ingestion for technical documentation into the authoritative DB.
- **kb_diagnostic.py**: Knowledge base verification and query testing tool.
- **cleanup_vectors.py**: Maintenance tool for vector database optimization and hard deletion.

---

## Project Status
- **Cloud Migration**: Completed.
- **Role-Based Access Control (RBAC)**: Active (Admin/Staff roles).
- **Ticket Auto-Assignment**: "Least Busy" load balancer implemented inside SQLLogger.
- **Hardware Ready**: CPU-only VM compatible.
- **AI Models**: Standardized on production-ready `gpt-4o-mini` (Categorization) and `gpt-5-mini` (Generation).
