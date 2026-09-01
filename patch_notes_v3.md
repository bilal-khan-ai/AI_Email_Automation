# 🚀 AI Email Automation — Version 3.0 Patch Notes
**Release Date:** 31 August 2026  
**Branch:** Staged (Pre-commit)  
**Scope:** 32 files changed · 8,980 insertions · 4,777 deletions

---

> [!IMPORTANT]
> This is a major architectural release. The monolithic `main.py` and `dashboard_socketio.py` have been decomposed into modular Flask Blueprints and a dedicated SocketIO handlers module. All existing functionality is preserved and has been significantly extended.

---

## 🏗️ Architecture — Backend Refactoring

### Flask Blueprint Decomposition
The previously monolithic `main.py` (~558 lines net change) has been split into six focused Blueprint modules under the new `routes/` package:

| Blueprint File | Route Prefix | Responsibility |
|---|---|---|
| `routes/ticket_routes.py` | `/api/tickets`, `/api/...` | Full ticket CRUD, send-to-customer, AI regeneration |
| `routes/devops_routes.py` | `/api/devops/...` | Azure DevOps integration endpoints (9 routes) |
| `routes/attachment_routes.py` | `/api/...` | Inline image upload/serve, attachment view/download |
| `routes/auth_routes.py` | `/login`, `/logout`, `/admin/...` | Authentication & user management |
| `routes/admin_config_routes.py` | `/api/holidays`, `/api/client_groups` | Holiday calendar & client group admin |
| `routes/core_routes.py` | `/` | Dashboard root, health endpoints |

A `routes/__init__.py` (23 lines) cleanly registers all blueprints on the Flask app with a single `register_all_blueprints(app)` call, keeping `main.py` as a lean application factory.

### SocketIO Handler Decomposition
`dashboard_socketio.py` dropped **1,560 lines** — its event handlers have been moved to the new `socketio_handlers.py` (360 lines). Every handler is now registered via `register_handlers(socketio, ...)` and decorated with purpose-built `@socketio_login_required` / `@socketio_admin_required` guards.

New SocketIO events introduced:
- `typing_update` → broadcasts real-time typing presence to peers editing the same ticket
- `get_staff_metrics` / `get_client_stats` / `get_drilldown_metrics` → admin analytics panels
- `get_ticket_timeline` → SLA event timeline per ticket
- `get_settings` / `update_settings` → live .env management (whitelisted keys only)
- `reassign_ticket` → admin-only live ticket reassignment
- `get_tickets` → resilient ticket list with live lock state injection

### Dashboard Context Module
New `dashboard_context.py` (326 lines) centralises all context-building logic — stats calculation, lock injection, and ticket hydration — previously scattered across `main.py` and the SocketIO file.

---

## 🔗 Azure DevOps Integration (New Module)

### `modules/devops_connector.py` — `AzureDevOpsConnector` class
Entirely new module (473 lines) providing a full-featured Azure DevOps REST API client:

- **Multi-project support** — `list_projects()` enumerates all accessible ADO projects; the UI project-picker populates from this live list, defaulting to the `.env`-configured project.
- **Work item creation** — `create_work_item(type, fields, attachment_urls)` supports Bug, Task, User Story, and Issue types. Bugs additionally accept `severity`, `repro_steps`, and `system_info` fields.
- **Batch retrieval** — `get_work_items_batch(ids)` fetches multiple items in one API call, reducing latency for tickets with many linked items.
- **Attachment upload** — `upload_attachment(project, file_data, filename)` pre-uploads binary files to ADO's attachment store and returns a URL that can be embedded into the work item on creation. Supports images, PDFs, and arbitrary files.
- **User search** — `search_users(query, limit)` and `get_organization_users()` enable assignee autocomplete in the creation drawer.
- **Active sprint detection** — `get_active_iteration(team)` automatically populates the Iteration Path field with the current sprint, reducing manual entry.
- **Per-project metadata** — `get_metadata_for_project(project)` fetches area paths and iteration paths for the selected project dynamically, so the dropdowns always reflect the correct project hierarchy.
- **Auth resilience** — supports both PAT and AAD token auth with `_refresh_aad_token()`. Includes circuit-breaker logic for auth failures inherited from the Graph connector pattern.

### New REST Endpoints (`routes/devops_routes.py`)
| Endpoint | Method | Description |
|---|---|---|
| `/api/devops/linked/<ticket_id>` | GET | Fetch all work items linked to a ticket |
| `/api/devops/projects` | GET | List all accessible ADO projects |
| `/api/devops/meta` | GET | Area paths + iterations for default project |
| `/api/devops/meta/<project_name>` | GET | Area paths + iterations for a specific project |
| `/api/devops/upload_attachment` | POST | Upload file to ADO, return attachment URL |
| `/api/devops/create` | POST | Create + link new work item to a ticket |
| `/api/devops/users` | GET | Search assignable org users |
| `/api/devops/link` | POST | Link an existing work item ID to a ticket |
| `/api/devops/unlink` | POST | Remove a work item link from a ticket |

### Real-time State Sync
A new `socket.on('devops_item_updated')` listener on the frontend watches for server-pushed state changes. When a work item transitions state in ADO (e.g. Active → Resolved), a toast notification appears: *"DevOps #1234: Active ➔ Resolved"*, and if the affected ticket's modal is currently open, the linked items panel automatically refreshes.

---

## ✏️ Tiptap v2 Rich Text Editor

The plain `<textarea>` reply box has been replaced with a **Tiptap v2**-based rich text editor, initialised via `initTiptapEditor()` called from `DOMContentLoaded`.

### Features
- **Full formatting toolbar** — Bold (`Ctrl+B`), Italic (`Ctrl+I`), Underline (`Ctrl+U`), Strikethrough, separated by thin dividers
- **Lists** — Bullet list and numbered ordered list
- **Blockquote** — visual indented quote block
- **Link insertion** — prompt-based hyperlink embedding
- **Inline image support** — click the image toolbar button to pick a file; the image is uploaded to `/api/upload_inline_image` and embedded as a `<img>` tag within the editor content
- **Paste-to-upload** — images pasted from clipboard are automatically uploaded and embedded
- **Keyboard shortcuts** — all standard Tiptap shortcuts work out of the box
- **Theme-aware** — editor background, border, and text colours are driven by CSS variables (`--input-bg`, `--text-primary`, `--border-color`), fully respecting light/dark mode

### Tiptap in the DevOps Drawer
Both the **Description** and **Repro Steps** fields in the Azure DevOps Create Drawer are also Tiptap editors (separate instances: `devopsDescEditor` and `devopsReproEditor`), each with their own toolbar. The HTML content is extracted via `editor.getHTML()` and sent to ADO directly. Images in these editors are also upload-backed via `/api/upload_inline_image`.

---

## 🗂️ Gmail-Style Attachment Tiles

### UI Change
Attachments in ticket message threads are no longer rendered as a plain list of links. They now render as **compact visual tiles** in a CSS Grid/Flexbox layout:

- Each tile is **175 × auto px** with a **105px preview area** at the top
- **Image files** show a cropped thumbnail (`object-fit: cover`) that zooms slightly on hover (`scale(1.04)`)
- **Non-image files** (PDF, DOCX, etc.) show a contextual icon (font-awesome icon matched by extension) in the preview area
- **Hover overlay** — a blurred dark overlay (`backdrop-filter: blur(2px)`) fades in with an open/download action indicator
- **Info strip** — file name (truncated with ellipsis), file size, and an action icon sit below the preview
- **Micro-animations** — tile lifts `translateY(-2px)` on hover with a deepened box-shadow; dark-mode shadow is boosted to `rgba(0,0,0,0.4)`
- Tiles are attached to the new `attachments-grid` flex container that wraps responsively

---

## 📊 Ticket Analytics Engine (New Module)

### `modules/ticket_analytics.py` — `TicketAnalytics` class
787-line new module providing the full analytics backend:

- **`log_ticket_event`** — appends a timestamped audit event to the `ticket_events` table for any ticket lifecycle action
- **`log_response_sent`** — specific shortcut for response/email-sent events, used by the send-to-customer flow
- **`get_business_seconds`** — SLA-aware elapsed time calculator that deducts weekends and configured holidays from the raw timestamp delta. Used to produce accurate SLA metrics.
- **`get_staff_metrics(username, time_range)`** — returns per-agent KPIs: tickets handled, avg response time, resolution rate, SLA compliance %, filtered by day/week/month/custom range
- **`get_all_staff_metrics`** — aggregates the above for a list of staff usernames in a single call (used by the admin metrics panel)
- **`get_client_stats(time_range, domain_filter)`** — volume and resolution metrics grouped by client email domain, with optional drill-down into a single domain
- **`get_drilldown_metrics`** — combined staff/client drilldown triggered from the UI modal
- **`get_ticket_timeline(ticket_id)`** — ordered list of all SLA-relevant events for a ticket: received, first-response, resolution, re-opened — used by the new Timeline Modal
- **Holiday Management** — full CRUD (`get_holidays`, `add_holiday`, `update_holiday`, `delete_holidays`) backed by a `holidays` DB table. Holidays are excluded from SLA business-hour calculations.
- **`get_weekly_analytics_summary`** — aggregated weekly snapshot for the dashboard summary widget

---

## 🗃️ Ticket Repository (New Module)

### `modules/ticket_repository.py` — 1,352 lines
Full data access layer for tickets, replacing direct SQL in `sql_logger.py`:

- `get_all_linked_devops_work_items()` — cross-ticket query returning all linked ADO items with their project context; used by the background polling worker to check for state changes
- `link_devops_work_item(ticket_id, work_item_id, actor, project)` — atomically appends a new `{id, project}` entry to `devops_work_item_ids` JSON column with duplicate check and full audit trail
- `unlink_devops_work_item(ticket_id, work_item_id, actor)` — removes the entry and logs an `DEVOPS_ITEM_UNLINKED` audit event
- `_convert_ticket_row` / `_convert_message_row` — normalise PostgreSQL datetime objects to ISO strings, deserialise JSONB fields, and convert `bool` to `0/1` for frontend compatibility
- All writes use `FOR UPDATE` row-level locking to prevent race conditions in concurrent environments

### `modules/sql_logger.py` — Mass Cleanup
2,630 lines dropped from `sql_logger.py` as analytics and repository methods were migrated to their dedicated modules. The file is retained for legacy compatibility but is now a thin facade.

---

## 🗄️ Database — PostgreSQL Connection Pooling

### `modules/db_connection.py` — New Module (162 lines)
- **`PostgreSQLConnectionManager`** — thread-safe connection pool using `psycopg2.pool.ThreadedConnectionPool` with configurable `min_conn` / `max_conn` (default 1–10)
- `get_connection()` context manager returns a pooled connection and automatically returns it on exit, even on exception
- `close_all()` safely drains the pool on app shutdown
- **`ActionType`** — string constant class for all audit event type names (`DEVOPS_ITEM_LINKED`, `DEVOPS_ITEM_UNLINKED`, `TICKET_CLOSED`, etc.)
- **`ActorType`** — `USER` / `SYSTEM` distinction for audit log attribution

---

## 🔄 Refresh Button with Relative Timestamps

### UX Change
The dashboard ticket list refresh control has been upgraded from a bare auto-refresh interval to a fully interactive experience:

- **Dedicated refresh button** (`#btnRefreshTickets`) with a styled icon (`#refreshIcon`) that spins (`fa-spin`) during the refresh operation
- **Animated refresh indicator** — a pill-shaped toast `#refreshIndicator` slides up from the bottom-right corner (`translateY(10px) → 0, opacity 0 → 1`) during both manual and auto-refresh cycles, then fades out after 2 seconds
- **Relative timestamp tooltip** — hovering over the refresh button triggers `updateRefreshTooltip()` which computes and sets the `title` attribute dynamically:
  - < 5 s → *"Refreshed just now"*
  - < 60 s → *"Refreshed N seconds ago"*
  - < 60 min → *"Refreshed N mins ago"*
  - < 24 h → *"Refreshed N hours ago"*
  - ≥ 24 h → *"Refreshed N days ago"*
- `lastRefreshedTimestamp` is updated after each successful `loadTickets()` call
- Auto-refresh interval (every 30 s) is annotated inline and unchanged in cadence

### CSS Additions
- `.refresh-btn-icon` — 38×38px square icon button with 12px border-radius, smooth `transition: all 0.25s`
- Hover state: green accent in light mode (`#2d6a4f`), neon-green in dark mode (`#4ade80`)
- `.refresh-indicator.show` — uses CSS transition (opacity + transform) for a native feel without JS animation libraries

---

## 🪟 Resizable Azure DevOps Drawer

### Drawer UI
The new **Create Azure DevOps Work Item** drawer (`#devopsCreateDrawer`) slides in from the right edge of the ticket modal. It supports:

- **Left-edge resize handle** (`#devopsDrawerResizer`) — a draggable `<div>` strip on the left side of the drawer. Dragging it left/right adjusts the drawer's `width` in real-time with `mousemove` tracking. Min/max constraints prevent the drawer from becoming unusably narrow or obscuring the ticket content.
- The drawer is layered above the main ticket modal (`z-index` stacked) and maintains scroll independently
- The form contains conditional **Bug-specific fields** (`#devopsBugFields`) — Severity select, Repro Steps Tiptap editor, and System Info textarea — shown/hidden via `onDevOpsTypeChange()` when the type is switched between Bug and other types

### Form Fields
- **Project** — live-populated from `/api/devops/projects`; changing selection triggers `onDevOpsProjectChange()` which re-fetches area paths and iteration paths for that project
- **Work Item Type** — Bug 🪲, Task 📋, User Story 📖, Issue ⚠️
- **Title** — required text input
- **Description** — Tiptap editor with image upload
- **Assigned To** — autocomplete backed by `/api/devops/users`
- **Area Path / Iteration Path** — dropdowns populated per-project; iteration defaults to active sprint
- **Priority** — 1–4
- **Tags** — free-text comma-separated
- **Attachment upload** — file picker; files are pre-uploaded to ADO on form submit, URLs injected into the creation payload

---

## 🔑 Authentication & User Management Routes

Extracted into `routes/auth_routes.py`:

| Route | Description |
|---|---|
| `GET/POST /login` | Standard credential login |
| `GET /logout` | Session clear |
| `GET /management` | Admin-only management page |
| `POST /admin/users/toggle-status/<username>` | Enable/disable user account |
| `POST /admin/users/toggle-assignable/<username>` | Mark user as ticket-assignable |
| `POST /admin/users/create` | Create new user account |
| `POST /admin/users/<username>/delete` | Delete user |
| `POST /admin/users/<username>/change-password` | Admin password reset |
| `POST /api/user/change-password` | Self-service password change |

---

## 📅 Holiday Calendar & Client Group Admin APIs

New `routes/admin_config_routes.py` adds full CRUD for SLA calendar management:

- `GET/POST/PUT/DELETE /api/holidays` — list, add, update, and bulk-delete holidays from the SLA business-hours calculator
- `POST /api/holidays/import` — bulk CSV import of holiday dates
- `GET /api/client_groups` — list all client domain groups
- `POST /api/client_groups` — create a new domain group
- `POST /api/client_groups/merge` — merge two existing groups
- `DELETE /api/client_groups/<id>` — remove a group

---

## 🖼️ Attachment Serving Routes

New `routes/attachment_routes.py`:

- `POST /api/upload_inline_image` — accepts a multipart image, saves to disk, returns a relative URL for Tiptap `src` embedding
- `GET /api/inline_image/<filename>` — serves the uploaded inline image (unauthenticated, filename-based)
- `GET /api/view_attachment` — proxies a Graph API attachment for in-browser preview (authenticated)
- `GET /api/download_attachment` — force-downloads an attachment with correct `Content-Disposition`
- `GET /.well-known/appspecific/com.chrome.devtools.json` — returns 204 to silence Chrome DevTools noise

---

## 📦 Vector Database — Documentation Improvements

`modules/vector_db.py` received comprehensive JSDoc-equivalent docstrings across all public methods of both `VectorDatabase` and `BookVectorDB`:

- `update_ticket_status` and `update_authority_status` — now document all parameters and return values
- `BookVectorDB` class docstring updated to reflect its dual role: BookStack + product documentation
- `query_bookstack`, `add_to_bookstack`, `search_similar`, `delete_from_bookstack`, `query_troubleshooting`, `query_visual`, `query_failure_modes` — all now have full parameter/return docstrings
- `get_deleted_count()` — new method returning the count of soft-deleted documents in the deleted collection

---

## 🚢 Deployment Scripts (New)

### `deploy_local.sh`
Docker Compose local deployment script with two modes:
- `./deploy_local.sh build` — full container rebuild with `--build`
- `./deploy_local.sh update` (default) — restart web + worker without rebuild

Post-deploy: prunes build cache (`docker builder prune -f`, `docker image prune -f`) and prints live resource stats via `docker stats --no-stream`.

### `safe_deploy_local.sh`
Safe variant targeting **only the `web` and `worker` services** — leaves the database container running untouched if it's healthy, and starts it if stopped. Prevents accidental DB restarts during rapid iteration cycles.

Both scripts:
- Auto-detect `docker compose` vs `docker-compose` CLI
- Validate `.env` presence before proceeding
- Exit cleanly with descriptive error messages

---

## 🐛 Bug Fixes

| Area | Fix |
|---|---|
| `graph_connector.py` | Minor patch (13 lines) — likely auth header or token refresh edge case |
| `openai_agent.py` | 2-line fix — likely model string or response parsing correction |
| `pii_redactor.py` | 3-line fix — edge case in PII pattern matching |
| `modules/env_manager.py` | 18-line fix — safer `.env` read/write with whitelist enforcement |
| `auth.py` | 6-line fix — session or user lookup hardening |
| `config.py` | `Config.reload()` now properly re-reads `.env` after `update_settings` socket event |
| `Pages/login.html` | 2-line fix (minor UI or redirect correction) |
| `Pages/admin_users.html` | 8-line fix (user management panel correction) |
| `Pages/management.html` | 14-line fix (settings panel alignment or field fix) |
| DevOps work item links | Duplicate-link guard added: checks existing IDs before appending to `devops_work_item_ids` JSON array |
| Attachment tile hover | Overlay now uses `backdrop-filter: blur(2px)` to prevent text bleed-through on complex backgrounds |

---

## 📐 CSS System Additions (Summary)

| Class | Purpose |
|---|---|
| `.refresh-btn-icon` | Styled 38×38px icon refresh button with green hover glow |
| `.refresh-indicator` / `.show` | Animated bottom-right pill toast for refresh events |
| `.close-btn` | Unified close button with red glow on hover (light + dark mode) |
| `.attachments-grid` | Flex container for attachment tile layout |
| `.attachment-tile` | 175px card with lift-on-hover and border accent |
| `.attachment-tile-preview` | 105px image thumbnail area with `object-fit: cover` |
| `.attachment-tile-img` | Scale-on-hover image with cubic-bezier transition |
| `.attachment-tile-overlay` | Blurred action overlay, opacity-0 to opacity-1 on hover |
| `.attachment-tile-info` | File name + metadata strip below preview |
| `.tiptap-wrapper` | Bordered Tiptap editor container |
| `.tiptap-toolbar` / `.toolbar-btn` | Toolbar button system with active state and dividers |
| `.devops-drawer` | Right-sliding full-height creation drawer |
| `.devops-drawer-resizer` | Left-edge drag handle for drawer width resizing |

---

## 📈 Change Metrics

| File Category | Files | Net Change |
|---|---|---|
| New modules | 7 | +3,800 lines |
| Route blueprints | 6 | +1,300 lines |
| Frontend (HTML/CSS/JS) | 3 | +3,437 lines |
| SocketIO handlers | 2 | +360 / −1,560 |
| Deploy scripts | 2 | +102 lines |
| Config & Auth | 3 | +31 lines |
| **Total** | **32** | **+8,980 / −4,777** |
