# AI Email Automation - Technical Reference Manual

## 📂 Project Architecture Tree
```text
AI_Email_Automation/
├── .agents/
│   └── project_context.md          # Central technical documentation (this file)
├── data/                           # Local storage for DBs and artifacts
├── modules/                        # Core functional components
│   ├── doc_processor.py            # .docx and .pdf parsing engine
│   ├── env_manager.py              # Atomic .env file transaction handler
│   ├── graph_connector.py          # Microsoft Graph API resilient interface
│   ├── image_processor.py          # Vision-based OCR and description
│   ├── openai_agent.py             # Multi-stage reasoning and RAG engine
│   ├── pii_redactor.py             # Presidio-based data scrubbing
│   ├── sql_logger.py               # PostgreSQL persistence and audit system
│   ├── tables_processor.py         # Tabular data extraction (xlsx/csv)
│   └── vector_db.py                # ChromaDB manager with VRAM safety
├── pages/                          # Jinja2 HTML Templates
│   ├── admin_users.html            # Staff provisioning interface
│   ├── dashboard.html              # Main support ticket dashboard
│   ├── login.html                  # Authentication entry point
│   └── management.html             # Admin console and system settings
├── static/                         # Frontend assets (JS/CSS)
│   ├── admin_users/                # Assets for staff management
│   ├── dashboard/                  # Assets for the main dashboard
│   ├── login/                      # Assets for authentication
│   └── management/                 # Assets for admin console
├── auth.py                         # RBAC and session authentication
├── config.py                       # Centralized environment configuration
├── dashboard_socketio.py           # Real-time WebSocket bridge (Backend)
├── main.py                         # Central ingestion and daemon engine
├── manage_users.py                 # CLI tool for staff provisioning
├── ticket_cleanup.py               # Database maintenance and TTL manager
└── .env                            # Environment secrets (Not in Tree)
```

---

## 🛠️ Module Technical Breakdown

### 🛰️ `main.py` (Core Ingestion Engine)
Acts as the central lifecycle manager for support tickets, bridging the Graph API and PostgreSQL.
*   **SupportAgent Class [L33-316]**: The main engine orchestrating the ingestion and processing flow.
*   **Email Polling Loop [L104-142]**: Periodic background task fetching latest emails from Outlook.
*   **Ticket Ingestion Pipeline [L144-245]**: Multi-stage process including PII redaction and initial state analysis.
*   **Subject-based Matching [L247-285]**: Logic for linking new replies to existing tickets via subject heuristics.
*   **Background Maintenance [L287-316]**: Automatic cleanup of expired tokens and temporary file buffers.
*   **Application Entry [L319-380]**: Service startup, daemon initialization, and signal handling.

### 🔌 `dashboard_socketio.py` (Real-time Backend)
The WebSocket layer providing low-latency updates to the web frontend.
*   **SocketIO Setup & Auth [L1-54]**: Handshake logic and session validation for real-time connections.
*   **Ticket Management [L100-350]**: Event handlers for assignment, status changes, and note additions.
*   **SLA & Performance [L350-450]**: Dynamic calculation and broadcasting of real-time SLA metrics.
*   **Real-time Broadcasts [L552-602]**: Efficient delta-updates to client-side dashboards to prevent full reloads.
*   **Backend Poller [L604-648]**: Integration bridge for receiving updates from the `main.py` daemon.

### 🧠 `modules/openai_agent.py` (AI Reasoning Engine)
The brain of the system, responsible for RAG-augmented response generation and intent classification.
*   **Interpretation Layer [L96-138]**: Classifies intent, urgency, and requirements using `gpt-4.1-nano`.
*   **Issue State Engine [L140-201]**: Consolidates thread history into technical "Issue States" for precise RAG.
*   **Response Generation [L326-520]**: Multi-source RAG (Docs + Experience) using `gpt-5-mini` with sendability gates.
*   **Ticket Summarization [L522-552]**: Brief, high-signal summaries of customer issues for the dashboard.
*   **Intent Categorization [L554-599]**: Statistical classification of tickets into functional buckets.
*   **Vision/OCR Analysis [L699-752]**: Direct integration with OpenAI Vision for attachment analysis.

---

## 🎨 Frontend Architecture

### 📄 `pages/` (HTML Templates)
Jinja2 templates providing the structure for the web-based management system.
*   **`dashboard.html` [L1-366]**: Primary interface featuring real-time ticket cards, modal for conversation history, and AI draft composer.
*   **`management.html` [L1-579]**: Administrative hub for staff performance analytics, system-wide settings, and ticket re-routing.
*   **`login.html` [L1-70]**: Secure login interface with robust feedback for authentication failures.
*   **`admin_users.html` [L1-248]**: Dedicated management page for staff credentialing and role-based access control.

### ⚡ `static/` (Client-side Logic)
JavaScript and CSS files providing interactivity and premium aesthetics.
*   **`dashboard.js` [L1-1800+]**: Orchestrates real-time UI synchronization, ticket locking logic, modal state management, and interactive reporting charts with date filtering.
*   **`management.js` [L1-447]**: Drives administrative dashboards, rendering performance charts and live system-wide activity logs.
*   **`login.js` [L1-30]**: Client-side validation and secure form handling for the authentication gateway.

---

## 🗄️ Core Data & Infrastructure

### `ticket_cleanup.py` (Database Maintenance)
Standalone daemon/script for TTL and archival management.
*   **Soft-Delete Archival**: Preserves ticket metadata for reporting integrity while purging heavy payload data (messages and attachments).

### `modules/sql_logger.py` (Persistence Layer)
Handles the relational schema, audit logs, and high-concurrency connection pooling.
*   **Connection Management [L41-128]**: Thread-safe PostgreSQL pooling using `ThreadedConnectionPool`.
*   **Schema Initialization [L153-412]**: Automated table creation (including the `holidays` table) and migrations.
*   **Ticket Lifecycle Logs [L414-798]**: Idempotent ticket creation and message persistence with `internet_message_id`.
*   **SLA Business Seconds [L614-650]**: Custom Python business hours/seconds calculation excluding weekends and public holidays (`get_business_seconds`).
*   **Analytics Engine [L1683-1979]**: Aggregation of staff performance, domain stats, and ticket timelines.

### `modules/vector_db.py` (RAG Backbone)
Manages semantic storage and retrieval with a focus on memory efficiency.
*   **VRAM Management [L26-89]**: Context-manager ensuring embedding models are unloaded immediately after use.
*   **Batch Ingestion [L217-291]**: Optimized multi-document insertion for bulk processing.
*   **Soft-Delete Enforcement [L362-517]**: Maintains semantic index parity with the SQL persistence layer.

### `modules/graph_connector.py` (Outlook Integration)
Resilient interface for Microsoft Graph API interactions.
*   **Retry Config [L59-108]**: Advanced exponential backoff with random jitter for API resilience.
*   **Asyncio Worker [L165-638]**: Background thread managing all non-blocking API calls to prevent UI stalls.
*   **Email Threading [L548-614]**: Dedicated logic for preserving conversation integrity using Graph API reply endpoints.

---

## 🛡️ Security & Processing

### `modules/pii_redactor.py` (Privacy scrubbing)
*   **Indian PII Suite [L40-123]**: Specialized recognizers for PAN, Aadhaar, GSTIN, and IFSC codes.
*   **Redaction Operators [L161-175]**: Granular mapping of sensitive entities to masked placeholders.

### `modules/doc_processor.py` (Document Parser)
*   **Docx Extraction [L144-232]**: Recursive extraction of text, tables, and images from Word attachments.
*   **PDF Processing [L238-345]**: Hybrid approach using text extraction and vision-based OCR for scanned pages.

---
> [!NOTE]
> This documentation is dynamically maintained. Line ranges are verified as of April 29, 2026.