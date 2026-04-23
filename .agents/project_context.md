# AI Email Automation Project Context

This document provides a quick reference for the project's file structure, core features, and detailed file descriptions.

---

## 🏗️ Core Application Files

### [main.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/main.py)
Core application entry point and lifecycle manager for the Support Agent.
- **SupportAgent Initialization**: Orchestrates all modules (SQL, Graph, Vector DBs, AI, Processors). `[Line 154]`
- **Signal Handling**: Graceful shutdown on SIGINT/SIGTERM. `[Lines 235-242]`
- **Ticket Cleanup Daemon**: Background thread for automated ticket archival and removal. `[Lines 244-301]`
- **Ticket Lifecycle Management**: Soft-delete after 1 day; Hard-delete after 6 days. `[Lines 303, 343]`
- **Live Polling Loop**: Periodic fetching of new emails from MS Graph. `[Line 798]`
- **PII Redaction**: Automatic stripping of sensitive info before RAG ingestion. `[Line 455]`

### [dashboard_socketio.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/dashboard_socketio.py)
Real-time WebSocket server and Flask application logic.
- **Template Folder**: Configured to use the `/Pages` directory. `[Line 21]`
- **Real-time Synchronization**: Broadcasts ticket updates, locks, and status changes via SocketIO. `[Line 30]`
- **Ticket Status Management**: Toggles between 'Open' and 'Closed' statuses. `[Line 527]`
- **AI Regeneration**: Handles synchronous AI draft regeneration with user feedback. `[Line 1045]`
- **Attachment Serving**: Authenticated endpoints for viewing/downloading attachments. `[Lines 729, 773]`

### [auth.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/auth.py)
User authentication, role-based access control, and session management.
- **UserManager**: Database-backed user management with password hashing. `[Line 42]`
- **RBAC**: Protects routes using `@login_required` and `@admin_required`. `[Lines 575, 591]`
- **Assignable Flag**: Controls which users appear in assignment dropdowns. `[Line 101]`

### [config.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/config.py)
Centralized configuration loader from environment variables.
- **Credential Management**: Azure, OpenAI, and Database connection strings.
- **Feature Flags**: Toggles for AI generation, test mode, and logging levels.

---

## 🧩 Modules (`/modules`)

### [sql_logger.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/modules/sql_logger.py)
PostgreSQL backend for ticket persistence and event logging.
- **Human-Readable Timeline**: Logs all user actions (Take, Save, Send) for the SLA timeline. `[Line 1324]`
- **Staff Metrics**: Aggregated performance data (Response time, Wait time, Resolution time). `[Line 901]`
- **Message Deduplication**: Uses `internet_message_id` to prevent duplicate ingestion. `[Line 1445]`

### [openai_agent.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/modules/openai_agent.py)
AI orchestration for RAG and vision tasks.
- **Dual-Context RAG**: Prioritizes BookStack docs over historical experience. `[Line 368]`
- **Interpretation Layer**: Categorizes intent and urgency before generation. `[Line 96]`
- **Vision Integration**: Routes all image analysis and OCR to OpenAI models. `[Line 634]`

### [graph_connector.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/modules/graph_connector.py)
Resilient interface for Microsoft Graph API.
- **Async Workers**: Offloads API calls to prevent blocking the event loop. `[Line 164]`
- **Smart Ingestion**: Fixes broken CID image links and fetches attachments dynamically. `[Line 349]`

### [env_manager.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/modules/env_manager.py)
Transactional management of the `.env` configuration file.
- **Atomic Updates**: Ensures configuration changes are saved safely without corruption. `[Line 31]`
- **Comment Preservation**: Maintains existing comments and formatting in the `.env` file. `[Line 46]`

### [vector_db.py](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/modules/vector_db.py)
Semantic search using ChromaDB.
- **Dynamic Device Selection**: CUDA GPU support with CPU fallback. `[Line 53]`
- **Soft-Delete**: Filters out inactive vectors from search results. `[Line 130]`

---

## 📄 UI Pages (`/Pages`)

### [dashboard.html](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/Pages/dashboard.html)
Main ticket management interface.
- **De-bloated Structure**: Contains only HTML and Jinja2 bridge variables.
- **Rich Interaction**: Iframe-based email rendering with Universal Dark Mode support.

### [management.html](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/Pages/management.html)
Admin console for performance metrics and staff control.
- **Performance Grids**: Visual metrics for agent response and customer wait times.
- **User Control**: Add/Remove staff and toggle assignable status.

### [login.html](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/Pages/login.html)
Secure entry point for the application.

### [admin_users.html](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/Pages/admin_users.html)
Granular user and role management interface.

---

## 🎨 Static Assets (`/static`)

Organized into page-specific subdirectories for modularity and performance.

### **Dashboard Assets**
- **[dashboard.js](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/static/dashboard/dashboard.js)**: Extracted logic (1600+ lines) handling SocketIO, Quill editor, and SLA timelines.
- **[dashboard.css](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/static/dashboard/dashboard.css)**: Modern design system with Glassmorphism and Dark Mode tokens.

### **Management Assets**
- **[management.js](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/static/management/management.js)**: Performance chart rendering and socket-driven metric updates.
- **[management.css](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/static/management/management.css)**: Specialized layout for data-heavy administrative grids.

### **Login/Admin Assets**
- **[login.js](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/static/login/login.js)**: Form validation and password visibility toggling.
- **[admin_users.js](file:///c:/Users/Bilal/Desktop/AI_Email_Automation/static/admin_users/admin_users.js)**: Client-side validation for user creation and role updates.

---

## 🛠️ Utilities (`/utility`)
- **ingest.py**: Bulk historical email ingestion tool.
- **bookstack_ingest.py**: Sync tool for technical documentation.
- **kb_diagnostic.py**: Verification tool for Vector DB integrity.
- **cleanup_vectors.py**: Maintenance tool for hard-deleting vectors.
