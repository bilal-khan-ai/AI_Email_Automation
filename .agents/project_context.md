# AI Email Automation - Technical Reference Manual & Context

## 📂 Final Standardized Project Architecture Tree

```text
AI_Email_Automation/
├── .agents/
│   ├── project_context.md          # Central architecture & technical reference manual (this file)
│   └── ui_standardization.md       # Design system, CSS tokens, and UI governance rules
│
├── app/                            # 🌐 Core Flask Application Package
│   ├── __init__.py                 # Application factory (create_app)
│   └── auth/                       # Authentication & User Management
│       └── __init__.py             # Auth helpers & decorator exports
│
├── routes/                         # 🛣️ Modular HTTP Blueprints
│   ├── __init__.py                 # Blueprints registration & export
│   ├── core_routes.py              # Main dashboard index (/) & contacts/config APIs
│   ├── ticket_routes.py            # Ticket actions, send/reply endpoints & locking
│   ├── auth_routes.py              # User authentication, login, logout & admin user CRUD
│   ├── admin_config_routes.py      # Client groups, exchange holidays & settings endpoints
│   ├── devops_routes.py            # Azure DevOps work item link/sync endpoints
│   └── attachment_routes.py        # File & inline image attachment serving
│
├── services/                       # ⚙️ Business Logic & External Integrations
│   ├── __init__.py
│   ├── connectors/                 # 🔌 External API Integration Clients
│   │   ├── __init__.py             # Exports GraphConnector, AzureDevOpsConnector, OpenAIAgent
│   │   ├── graph_connector.py      # Microsoft Graph API client & non-retry worker loop
│   │   ├── devops_connector.py     # Azure DevOps REST client & work item sync
│   │   └── openai_agent.py         # Multi-stage reasoning, classification & RAG generation
│   ├── processors/                 # 📄 Content Processing & Extraction
│   │   ├── __init__.py             # Exports DocProcessor, TablesProcessor, ImageProcessor, redact_pii
│   │   ├── doc_processor.py        # PDF & Word document parser / OCR engine
│   │   ├── tables_processor.py     # Tabular data & Excel extraction
│   │   ├── image_processor.py      # Image optimizer & OCR
│   │   └── pii_redactor.py         # Sensitive entity scrubbing
│   └── background/                 # 🔄 Background Tasks & Daemons
│       ├── __init__.py
│       └── ticket_cleanup.py       # Automated ticket retention, TTL & purge daemon
│
├── data_access/                    # 💾 Database & Persistence Layer
│   ├── __init__.py                 # Exports PostgreSQLConnectionManager, TicketRepository, TicketAnalytics, VectorDatabase
│   ├── db_connection.py            # Threaded PostgreSQL connection pool manager & DB enums
│   ├── ticket_repository.py        # Relational schema management & ticket CRUD operations
│   ├── ticket_analytics.py         # SLA, FRT, resolution & staff performance metric engine
│   ├── vector_db.py                # ChromaDB vector store with OpenAI text-embedding-3-small
│   └── sql_logger.py               # Singleton bridge & backwards-compatible access layer
│
├── templates/                      # 📄 Standard Jinja2 HTML Templates
│   ├── dashboard.html              # Main support ticket dashboard & split panel
│   ├── management.html             # Administrative console, reports, clients, holidays & settings
│   ├── login.html                  # User login portal
│   └── admin_users.html            # User provisioning view
│
├── static/                         # 🎨 Frontend Assets
│   ├── shared/                     # Global styles: theme.css, components.css, theme.js
│   ├── dashboard/                  # Dashboard logic & styles: dashboard.css, dashboard.js
│   ├── management/                 # Management console logic & styles: management.css, management.js
│   └── login/                      # Login portal assets: login.css
│
├── scripts/                        # 🛠️ Maintenance, Operations & Ingestion
│   ├── deployment/                 # safe_deploy_local.sh, safe_deploy.sh, deploy.sh
│   ├── database/                   # manage_users.py, create_anil_db.sh, backups/
│   └── ingestion/                  # batch_migrate.py, bookstack_ingest.py, ingest.py, cleanup_vectors.py
│
├── modules/                        # 🛡️ Backwards-Compatibility Facade (Lazy __getattr__ proxy)
├── config.py                       # Centralized environment configuration & dynamic reloader
├── dashboard_socketio.py           # Web service entry point (Gunicorn / Flask-SocketIO)
├── dashboard_context.py            # Global context singletons & lazy initialization bridge
├── main.py                         # Ingestion daemon & Graph API background loop
├── Dockerfile                      # Web & Worker container definition
└── docker-compose.yml              # PostgreSQL, Web & Worker container orchestration
```

---

## 🛠️ Architecture & Service Layer Breakdown

### 1. Web Layer (`app/`, `routes/`, `dashboard_socketio.py`)
* **Application Factory (`app/__init__.py`)**: `create_app()` initializes Flask, registers all route blueprints, binds SocketIO in eventlet async mode, and mounts the standard `templates/` directory.
* **HTTP Blueprints (`routes/`)**:
  * `core_routes.py`: Dashboard view (`/`), config info (`/api/config`), and contact auto-fill (`/api/contacts`).
  * `ticket_routes.py`: Ticket detail lookups, thread message listings, drafting replies, and direct sending via Microsoft Graph API.
  * `auth_routes.py`: Login, logout, session management, and admin user CRUD.
  * `admin_config_routes.py`: Client groups management, exchange holidays CRUD/NLP import, and runtime system settings REST API.
  * `devops_routes.py`: Linking, unlinking, and synchronizing Azure DevOps work items.
  * `attachment_routes.py`: Streaming and downloading inline/file attachments.
* **WebSocket Handlers (`socketio_handlers.py`)**:
  * Real-time ticket locking mutexes (`lock_ticket`, `unlock_ticket`).
  * Real-time re-assignments, status changes, and internal note broadcasts.
  * Live KPI and staff/client metric pushes (`staff_metrics_update`, `client_stats_update`).
  * Live system settings persistence (`update_settings` &rarr; `EnvManager.update_vars`).

### 2. Services Layer (`services/`)
* **`services/connectors/graph_connector.py`**:
  * Dedicated asynchronous worker managing Microsoft Graph API requests.
  * Strict duplicate prevention: `SEND_EMAIL` and `REPLY_TO_EMAIL` are single-dispatch operations without automatic multi-retries.
  * Correct reply format: Uses `request_body.comment = body_html` for Graph `/messages/{id}/reply`.
* **`services/connectors/devops_connector.py`**:
  * Interacts with Azure DevOps REST API for querying, creating, and linking work items directly to ticket threads.
* **`services/connectors/openai_agent.py`**:
  * Multi-stage reasoning pipeline: Intent classification, Issue State consolidation, RAG retrieval from ChromaDB, and response generation.
* **`services/processors/`**:
  * `doc_processor.py`: Text extraction and OCR for `.docx` and `.pdf` files.
  * `tables_processor.py`: Tabular data extractors for `.csv` and `.xlsx` attachments.
  * `image_processor.py`: Image optimization and vision description.
  * `pii_redactor.py`: Entity scrubbing for PII protection.
* **`services/background/ticket_cleanup.py`**:
  * Retention manager enforcing soft-delete and permanent purge policies based on `.env` configuration.

### 3. Data & Persistence Layer (`data_access/`)
* **`db_connection.py`**:
  * Thread-safe connection pool using `psycopg2.pool.ThreadedConnectionPool`.
  * Standardized `ActionType` and `ActorType` audit enums.
* **`ticket_repository.py`**:
  * PostgreSQL schema initializer (Tickets, Messages, Attachments, Notes, Audit Logs, Users, Holidays, Client Groups, DevOps Links).
  * High-concurrency CRUD operations with idempotent message deduplication (`internet_message_id`).
* **`ticket_analytics.py`**:
  * First Response Time (FRT) and Time to Resolution (TTR) analytics.
  * Business seconds calculations (`get_business_seconds`) accounting for working hours, weekends, and dynamically seeded exchange holidays.
* **`vector_db.py`**:
  * ChromaDB vector database manager utilizing OpenAI's `text-embedding-3-small` API.

---

## 🔐 Environment & Runtime Configuration

System settings are dynamically managed via `config.py` and `modules/env_manager.py`:
* **Live Updates**: Updating settings via `/management` writes directly to `.env` and triggers `Config.reload()` in memory without restarting containers.
* **Separated Polling Intervals**:
  * `POLLING_INTERVAL`: Microsoft Graph API email ingestion frequency.
  * `AZURE_DEVOPS_POLLING_INTERVAL`: Background sync frequency for Azure DevOps work items.
* **Sandbox / Test Mode**:
  * `TEST_MODE=True`: Redirects all outgoing emails to `TEST_EMAIL` and `TEST_CC` with `TEST_SUBJECT_TAG` prefix.