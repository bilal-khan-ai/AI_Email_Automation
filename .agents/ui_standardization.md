# UI Standardization Specification & Guidelines

This document outlines the **Standardized UI System**, design tokens, component architecture, and governance rules in effect across the AI Email Automation project.

---

## 🎨 1. Design System & Theming Architecture

The UI adheres to a **Google Antigravity Premium Glassmorphic** aesthetic, characterized by subtle translucency, curated HSL color tokens, harmonious green accents (`#2D6A4F` / `#52B788`), and fluid typography using the `Inter` typeface.

### Centralized Theme Tokens (`static/shared/theme.css`)

All colors and surface states are governed strictly by CSS variables:

| CSS Variable | Light Mode Value | Dark Mode Value (`body.dark-mode`) | Purpose |
|---|---|---|---|
| `--bg-main` | `#f4f6f8` | `#0f172a` (Deep Slate) | Overall viewport background |
| `--bg-card` | `#ffffff` | `#1e293b` (Card Slate) | Cards, modals & elevated surfaces |
| `--bg-card-header` | `#ffffff` | `#1e293b` | Card header background |
| `--bg-surface-subtle` | `#f8fafc` | `#1e293b` | Filter bars, table striping |
| `--text-primary` | `#1a202c` | `#f1f5f9` | Primary headings & high-contrast text |
| `--text-secondary` | `#4a5568` | `#94a3b8` | Subtext, labels & descriptions |
| `--text-muted` | `#718096` | `#64748b` | Input placeholders & timestamps |
| `--accent` | `#2D6A4F` (Forest Green) | `#52B788` (Emerald Bright) | Active tabs, primary buttons, borders |
| `--accent-hover` | `#1B4332` | `#74C69D` | Button hover & active states |
| `--border-color` | `#e2e8f0` | `#334155` | Borders, table dividers & inputs |
| `--input-bg` | `#ffffff` | `#0f172a` | Form controls, selects & textareas |
| `--radius-sm` / `--radius-md` / `--radius-lg` | `6px` / `10px` / `16px` | Same | Border radii for cards and pills |
| `--shadow-sm` / `--shadow-md` / `--shadow-lg` | Light Elevation Shadows | Dark Glowing Shadows | Depth & glassmorphic elevation |

---

## 🧩 2. Core Shared Component Library (`static/shared/components.css`)

All pages (`dashboard.html`, `management.html`, `login.html`) consume shared components from `static/shared/components.css`:

### A. Standardized App Header (`.app-header`)
```html
<div class="app-header">
    <div class="app-header-container">
        <div class="app-header-brand">
            <img src="{{ url_for('static', filename='management/gws_logo.png') }}" alt="Logo">
            <div class="app-header-title">Management Console</div>
        </div>
        <div class="app-header-actions">
            <button class="theme-toggle-btn" onclick="toggleTheme()"><i class="fa fa-moon"></i></button>
            <a href="/" class="btn btn-sm btn-outline-light"><i class="fa fa-arrow-left me-1"></i> Dashboard</a>
            <a href="/logout" class="btn btn-sm btn-danger"><i class="fa fa-right-from-bracket me-1"></i> Logout</a>
        </div>
    </div>
</div>
```

### B. Quick Navigation Bar (`.quick-nav`)
```html
<div class="quick-nav">
    <div class="nav-tab active" onclick="showSection('reports')"><i class="fa fa-chart-column me-1"></i> Reports</div>
    <div class="nav-tab" onclick="showSection('management')"><i class="fa fa-users-gear me-1"></i> Management</div>
    <div class="nav-tab" onclick="showSection('holidays')"><i class="fa fa-calendar-days me-1"></i> Holidays</div>
    <div class="nav-tab" onclick="showSection('settings')"><i class="fa fa-sliders me-1"></i> Settings</div>
</div>
```

### C. Animated Pill Slider Toggles (`.pill-toggle`)
```html
<div class="pill-toggle" id="reportViewPill" data-active="client">
    <div class="pill-option active" data-view="client">Client</div>
    <div class="pill-option" data-view="staff">Staff</div>
    <div class="pill-slider"></div>
</div>
```

### D. Unified Stat Cards (`.app-stat-card`)
```html
<div class="app-stat-card">
    <div class="stat-label"><i class="fa fa-ticket me-1"></i> Total Tickets</div>
    <div class="stat-value text-primary" id="totalTicketsCount">1,248</div>
    <div class="stat-subtext">Active tickets in current window</div>
</div>
```

### E. Status Toggles (`.status-toggle`)
* Custom animated toggle switch for boolean states (e.g. active users, ticket assignments):
```html
<div class="status-toggle active"></div>
```

---

## ⚡ 3. Unified Theme Controller (`static/shared/theme.js`)

Theme state is synchronized globally across all tabs using `localStorage('gws_theme')` and broadcast listeners:

```javascript
// Automatically executed on initial load:
initTheme();

// Available API:
toggleTheme();            // Toggles between 'light' and 'dark'
applyTheme('dark');       // Sets explicit mode
updateThemeButton(isDark);// Updates sun/moon icons
```

---

## 📐 4. UI Governance Rules to Follow

When making any frontend or styling changes, **ALWAYS** follow these rules:

1. **Do NOT Use Hardcoded Colors**:
   * ❌ Avoid `color: #666;`, `background: #fff;`, `border: 1px solid #ddd;`.
   * ✅ Use CSS variables: `color: var(--text-secondary);`, `background: var(--bg-card);`, `border: 1px solid var(--border-color);`.

2. **Always Support Dark Mode by Default**:
   * Verify all text, subtext (`.form-text`, `.text-secondary`, `.text-muted`), input fields, placeholders, and table headers adapt under `body.dark-mode`.

3. **FontAwesome 6 Vector Icons Over Emojis**:
   * ❌ Do not use raw emoji characters like `👥`, `⚙️`, `🗑️` for interactive buttons.
   * ✅ Use FontAwesome icons: `<i class="fa fa-users"></i>`, `<i class="fa fa-sliders"></i>`, `<i class="fa fa-trash-can"></i>`.

4. **Preserve Layout Hierarchy & Validate HTML Div Nesting**:
   * Every container (e.g. `#section-management`, `#managementStaffView`, `#section-settings`) must have explicitly matched opening and closing `<div>` tags to prevent sub-tab rendering collapse.

5. **Maintain Cache-Busting Version Query Strings**:
   * When updating CSS or JS files, update the asset version parameter in template `<head>` tags (e.g. `href="{{ url_for('static', filename='shared/components.css') }}?v=20260901_X"`).