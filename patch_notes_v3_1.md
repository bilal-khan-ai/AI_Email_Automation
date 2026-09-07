# 🚀 AI Email Automation — Version 3.1 Patch Notes
**Release Title:** Support Dashboard & Collaboration Update  
**Release Date:** 04 September 2026  
**Scope:** Core Dashboard, Modal Toolbar, Collaboration Engine, Media Delivery Pipeline  

---

> [!NOTE]
> **Version 3.1** focuses on real-time team collaboration, streamlined assignment workflows, proactive deadline tracking ("Due In"), design system harmonization in Dark Mode, and client-side media caching for lightning-fast queue browsing.

---

## 👥 1. Co-Worker Assignment & Team Collaboration

Support inquiries frequently require cross-functional collaboration between tier-1 support engineers, senior escalation staff, and domain experts. 

- **Secondary Collaborator Assignment (`co_worker`):** In addition to the primary ticket owner (`assigned_to`), tickets can now be assigned an active co-worker.
- **Database Architecture:** Backed by a dedicated `co_worker` column in the PostgreSQL `tickets` table with indexing for fast querying.
- **Dashboard Grid Badge:** Ticket cards on the main queue display a dedicated collaborator pill (`<i class="fa fa-user-friends"></i>`), allowing team leads to see both primary and secondary handlers at a glance.
- **Modal Header & Ticket Information Panel:** Real-time visibility inside the open ticket modal toolbar and the **Ticket Information** overview panel.
- **Seamless Reassignment & Unassignment:** One-click removal or reassignment with real-time UI synchronization via `/api/take_ticket`.

---

## 🔍 2. Typeahead Combobox for Staff Assignments

The legacy HTML `<select>` dropdowns for ticket assignments have been replaced with a high-performance **Typeahead Combobox**.

- **Instant Incremental Filtering:** Type to filter through staff and administrator accounts with sub-millisecond response.
- **Role Identity Badges:** Dropdown entries feature distinct visual role indicators (`Admin` in amber, `Staff` in muted slate) for clear organizational clarity.
- **Unassign & Reset Actions:** Dedicated quick-action item (`Unassigned` / `None (Remove)`) at the head of the dropdown list.
- **Hover Clear Button (`✕`):** A subtle, accessible clear icon appears on input hover for instant single-click field clearing.
- **Robust Event Architecture:**
  - Integrated `onmousedown="event.preventDefault()"` on the dropdown container and list items to permanently eliminate input-blur race conditions that previously caused dropped click events.
  - Full keyboard accessibility: navigate matches, hit `Enter` to confirm, or press `Escape` to dismiss.
  - Dropdown elevated to `z-index: 2200` to guarantee unobstructed layering above all modal surfaces.
  - Resilient DOM fallback: dynamically derives assignable users from server-rendered options if asynchronous background API calls are delayed.

---

## ⚡ 3. Quick Add Employee On-The-Fly

Eliminates workflow interruptions when assigning tickets to newly onboarded team members who have not yet been registered in the database.

- **Integrated Zero-State Detection:** When typing an employee name that yields no exact match, the dropdown presents a smart action item: **Add Employee "<name>"**.
- **In-Context Creation Dialog:** Clicking the action opens an integrated Quick Add dialog directly on top of the dashboard, removing the need to navigate away to the Admin Management Console.
- **Immediate Auto-Assignment:** Upon submission, the user is created with active assignable status, added to the active assignment list, and immediately assigned to the current ticket without a full page reload.

---

## ⏱️ 4. Due In Deadline Tracking & Audio-Player Controls

Proactively prevents SLA breaches with visual, real-time deadline monitoring on tickets requiring follow-up.

- **Human-Readable Live Countdown:** Real-time ticking timer (`formatLiveCountdown()`) cascading dynamically from months down to seconds:
  - *Active:* `2mo 5d 14h 30m 22s left` or `3h 45m 10s left`
  - *Overdue:* `Overdue (-3d 02h 15m 30s)` with bold red alert styling.
- **Flatpickr Calendar with Quick Presets:** Integrated calendar popup equipped with a rapid single-click preset bar:
  - Chip shortcuts: `+1h`, `+2h`, `+4h`, `+8h`, `+24h`, `+48h`.
- **Dynamic State Theming:**
  - **Running:** Primary brand accent with tabular numeric styling.
  - **Paused:** Muted slate styling for tickets awaiting customer feedback.
  - **Overdue:** High-contrast red alert styling across ticket cards and modal inputs.
- **Audio/Media-Player Style Action Menu:** Clicking an active timer opens a popover featuring **Pause**, **Resume**, **Stop**, and **Reset** controls along with formatted deadline timestamps.
- **Smart Send Interceptor:** Sending a reply on a ticket with an active deadline triggers a decision prompt, allowing the agent to either stop and clear the timer or keep it running.

---

## 🎨 5. UI/UX Polish & Dark Mode Harmonization

A comprehensive aesthetic review aligning the dashboard with the **Google Antigravity Premium** design philosophy.

- **Unified Dark Mode Palette:** Replaced hardcoded Tailwind slate-navy elements in Flatpickr and timer menus with native CSS theme tokens:
  - Cards: `--bg-card` (`#2d2d2d`)
  - Sub-surfaces & Headers: `--bg-surface-alt` (`#252525`)
  - Borders: `--border-color` (`#404040`)
  - Accents & Highlights: `--accent` (`#34d399`)
- **Ticket Information Panel Contrast:** Replaced washed-out `#383838` background with `--bg-surface-alt` (#252525) accented by an emerald left border (`4px solid var(--accent)`).
- **Chevron SVG Scoping (No More Zigzag Lines):** Scoped dropdown chevron background images strictly to native `select.modal-action-select` controls and enforced `background-image: none !important;` on text comboboxes, eliminating tiled background chevron artifacts.
- **Standardized Header Toolbar Dimensions:** Azure DevOps menu, Take Ticket button, Status selector, Comboboxes, Due In control, and Close button share a standardized height (34px), border radius (8px), and typography.
- **Dynamic Subject Marquee Scroller:** Clean overflow scroller in the modal title area ensuring long customer subject lines never break header alignment.

---

## 🖼️ 6. Client-Side Image Caching & High-Performance Media Serving

Drastically improves browsing performance and responsiveness when navigating attachment-heavy email threads.

- **Content-Addressable Deterministic Hashing:** Added `compute_image_content_hash` using SHA-256 over raw pixel bytes (stripping EXIF, IPTC, and container metadata) for 100% accurate deduplication across forwarded or duplicated attachments.
- **WebP Thumbnail Generation:** Incoming and previewed image attachments are automatically downscaled and stored as compressed WebP files on disk (`DATA_DIR/thumbnails/<hash>.webp`).
- **HTTP 304 Not Modified & ETag Validation:** Full `If-None-Match` and `ETag` support allows browsers to skip redownloading images on repeated ticket views.
- **Immutable Long-Term Cache Headers:** Static thumbnail responses include `Cache-Control: public, max-age=31536000, immutable`, enabling the client browser to serve images directly from local memory/disk cache for instantaneous thread rendering.
- **LRU Disk Budgeting:** Automatic cache size enforcement (`MAX_ATTACHMENT_CACHE_MB`) safely evicts the oldest cached files to ensure container storage stays within memory-constrained VM quotas.

---

## 📊 Summary of Modified Components

| Layer | Files | Key Enhancements |
|---|---|---|
| **Frontend Templates** | `templates/dashboard.html` | Combobox markup, Due In controls, preventDefault event hooks, modal headers |
| **Frontend Scripting** | `static/dashboard/dashboard.js` | Combobox typeahead, blur race condition fix, live countdown timer, quick presets |
| **Frontend Styles** | `static/dashboard/dashboard.css` | Dark mode tokenization, Flatpickr emerald theme, chevron scoping, 34px toolbar standard |
| **Media & Routing** | `routes/attachment_routes.py` | Content-hash caching, WebP thumbnail generation, ETag / 304 Not Modified headers |
| **Backend & Models** | `routes/ticket_routes.py`, DB | `co_worker` and `co_worker_time_limit` persistence via `/api/take_ticket` |
