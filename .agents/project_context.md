# AI Email Automation Project Context

This document provides a quick reference for the project's file structure, core features, and code locations.

---

## Root Directory

### [main.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/main.py)
Core application entry point and lifecycle manager for the Support Agent.

- **SupportAgent Initialization**: Orchestrates all modules (SQL, Graph, Vector DBs, AI, Processors). `[Line 154]`
- **Signal Handling**: Graceful shutdown on SIGINT/SIGTERM. `[Lines 235-242]`
- **Ticket Cleanup Daemon**: Background thread for soft and hard deletion of tickets. `[Lines 244-301]`
- **Email Ingestion (`ingest_email`)**: Handles PII redaction, attachment extraction (images/docs/tables), and ticket linking. `[Line 419]`
- **AI Response Generation (`generate_ai_response`)**: Dual RAG search (Docs + Experience) and GPT-4o-mini response drafting. `[Line 638]`
- **Live Polling Loop (`run_live`)**: Periodic fetching of new emails from MS Graph. `[Line 798]`

### [auth.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/auth.py)
User authentication, authorization, and session management.

- **UserManager Class**: Database-backed user management with PostgreSQL. `[Line 42]`
- **Password Hashing**: Secure storage using SHA-256 and unique salts.
- **Role-Based Access Control (RBAC)**: Supports 'admin' and 'user' roles.
- **Login Required**: Route protection for authenticated sessions.

### [dashboard_socketio.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/dashboard_socketio.py)
Real-time WebSocket events for the multi-user dashboard.

- **SocketIO Initialization**: Integrated with Flask for bi-directional communication. `[Line 24]`
- **Ticket Locking**: Prevents concurrent edits by different users. `[Line 626]`
- **Live Stats Calculation**: Real-time aggregation of open, closed, and total ticket counts. `[Line 91]`
- **Manual Email Responses**: Logic for sending emails directly from the dashboard. `[Line 435]`
- **Ticket Status/Assignment Updates**: Instant broadcasting of ticket changes to all clients. `[Lines 378, 381]`

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
- **Soft-Delete Implementation**: Tracking logic for temporary vs permanent deletion.
- **Metadata Management**: Logging of AI entities, draft statuses, and lock states.

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
- **Model Standard**: Uses `gpt-4o-mini` for cost-effective summarization and categorization.

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
- **Cloud Migration**: Completed. BLIP and EasyOCR offloaded to OpenAI Vision.
- **Hardware Ready**: CPU-only VM compatible (requirements.txt uses CPU torch).
- **GPU Optimized**: Code detects and uses CUDA if available for local embeddings.
- **AI Models**: Standardized on production-ready `gpt-4o-mini`.
