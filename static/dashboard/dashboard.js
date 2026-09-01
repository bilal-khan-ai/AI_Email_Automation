
let currentTickets = [];
let currentFilter = typeof userRole !== 'undefined' && userRole === 'admin' ? 'Open' : 'all';
let currentVisibility = typeof userRole !== 'undefined' && userRole === 'admin' ? 'all' : 'mine';
let currentTimeRange = 'all';
let customFromDate = null;
let customToDate = null;
let currentTicketRow = null;
let currentTicketIdStr = null;
// Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Declare Tiptap editor and DevOps state - start
let tiptapEditor = null;
let cachedDevOpsMeta = null;
// Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Declare Tiptap editor and DevOps state - end
let attachedFiles = [];
let currentAssignmentFilter = null;
let currentCustomerFilter = null;
let isTicketLocked = false;
let isRightPanelCollapsed = false;
let currentTicketStatus = null; // Added this as it was used but not declared globally in the snippet (or was it?)
let currentPage = 1;
const pageSize = 50;
let lastDisplayState = "";
let globalConfig = {};
async function fetchConfig() {
    try {
        const response = await fetch('/api/config');
        globalConfig = await response.json();
    } catch (err) {
        console.error("Error fetching config:", err);
    }
}
fetchConfig();

let allContacts = [];
async function fetchContacts() {
    try {
        const response = await fetch('/api/contacts');
        const data = await response.json();
        allContacts = data.contacts || [];
    } catch (err) {
        console.error("Error fetching contacts:", err);
    }
}

function setupAutocomplete(inputId, suggestionsId) {
    const input = document.getElementById(inputId);
    const suggestions = document.getElementById(suggestionsId);
    if (!input || !suggestions) return;
    
    let activeIndex = -1;

    input.addEventListener('input', () => {
        const parts = input.value.split(',');
        const value = parts[parts.length - 1].trim();

        if (value.length < 2) {
            suggestions.style.display = 'none';
            return;
        }

        const filtered = allContacts.filter(c =>
            c.toLowerCase().includes(value.toLowerCase())
        ).slice(0, 10);

        if (filtered.length > 0) {
            suggestions.innerHTML = filtered.map((c, i) =>
                `<div class="autocomplete-suggestion" data-index="${i}">${c}</div>`
            ).join('');
            suggestions.style.display = 'block';
            activeIndex = -1;
        } else {
            suggestions.style.display = 'none';
        }
    });

    suggestions.addEventListener('click', (e) => {
        const item = e.target.closest('.autocomplete-suggestion');
        if (item) {
            applySuggestion(input, item.textContent);
            suggestions.style.display = 'none';
        }
    });

    input.addEventListener('keydown', (e) => {
        if (suggestions.style.display === 'none') return;

        const items = suggestions.querySelectorAll('.autocomplete-suggestion');
        if (e.key === 'ArrowDown') {
            activeIndex = (activeIndex + 1) % items.length;
            updateActive(items);
            e.preventDefault();
        } else if (e.key === 'ArrowUp') {
            activeIndex = (activeIndex - 1 + items.length) % items.length;
            updateActive(items);
            e.preventDefault();
        } else if (e.key === 'Enter') {
            if (activeIndex > -1) {
                applySuggestion(input, items[activeIndex].textContent);
                suggestions.style.display = 'none';
                e.preventDefault();
            }
        } else if (e.key === 'Escape') {
            suggestions.style.display = 'none';
        }
    });

    document.addEventListener('click', (e) => {
        if (!input.contains(e.target) && !suggestions.contains(e.target)) {
            suggestions.style.display = 'none';
        }
    });

    function updateActive(items) {
        items.forEach((item, i) => {
            item.classList.toggle('active', i === activeIndex);
        });
    }

    function applySuggestion(targetInput, email) {
        const parts = targetInput.value.split(',').map(p => p.trim());
        parts.pop(); // Remove the partial string

        // Add the new email if not already present
        if (!parts.includes(email)) {
            parts.push(email);
        }

        targetInput.value = parts.filter(p => p !== '').join(', ') + ', ';
        targetInput.focus();
    }
}

const socket = io({ transports: ['websocket', 'polling'] });

function formatDate(isoString) {
    if (!isoString) return '';
    try {
        const date = new Date(isoString);
        return date.toLocaleString('en-GB', {
            hour: '2-digit',
            minute: '2-digit',
            hour12: true,
            day: '2-digit',
            month: 'short',
            year: 'numeric'
        });
    } catch (e) {
        return isoString;
    }
}

function getLocalDateString(dateObj) {
    if (!dateObj || isNaN(dateObj.getTime())) return '';
    const year = dateObj.getFullYear();
    const month = String(dateObj.getMonth() + 1).padStart(2, '0');
    const day = String(dateObj.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

function truncateTicketId(id) {
    if (!id) return 'NO-ID';
    if (id.length < 20) return id;
    return id.substring(0, 10) + '...' + id.substring(id.length - 6);
}

function toggleTheme() {
    const body = document.body;
    body.classList.toggle('dark-mode');
    const isDark = body.classList.contains('dark-mode');
    localStorage.setItem('darkMode', isDark);
    updateThemeButton(isDark);
}

function updateThemeButton(isDark) {
    const icon = document.getElementById('theme-icon');
    const text = document.getElementById('theme-text');
    if (icon && text) {
        if (isDark) {
            icon.textContent = '☀️';
            text.textContent = 'Light Mode';
        } else {
            icon.textContent = '🌙';
            text.textContent = 'Dark Mode';
        }
    }
}

/*
function initializeEditor() {
    if (!quillEditor) {
        quillEditor = new Quill('#editor', {
            theme: 'snow',
            modules: {
                toolbar: [
                    [{ 'header': [1, 2, 3, false] }],
                    ['bold', 'italic', 'underline', 'strike'],
                    [{ 'color': [] }, { 'background': [] }],
                    [{ 'list': 'ordered' }, { 'list': 'bullet' }],
                    [{ 'align': [] }],
                    ['link', 'image'],
                    ['clean']
                ]
            },
            placeholder: 'Compose your email response here...'
        });
    }
}
*/
function initializeEditor() {
    // Quill initialization is disabled
}

function toggleField(fieldId) {
    const field = document.getElementById(fieldId);
    field.classList.toggle('hidden');
}

function formatFileSize(bytes) {
    if (!bytes || bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

function cleanMessageBody(text) {
    if (!text) return '';
    if (text.includes('<') && text.includes('>')) {
        return text;
    }
    let div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function toggleRightPanel() {
    const panel = document.getElementById('rightPanel');
    isRightPanelCollapsed = !isRightPanelCollapsed;

    if (isRightPanelCollapsed) {
        panel.classList.add('collapsed');
    } else {
        panel.classList.remove('collapsed');
    }
}

function updateAttachedFilesList() {
    const container = document.getElementById('attachedFiles');
    const fileCount = document.getElementById('fileCount');

    if (!container || !fileCount) return;

    if (attachedFiles.length === 0) {
        container.innerHTML = '';
        fileCount.textContent = 'No files selected';
        return;
    }

    fileCount.textContent = `${attachedFiles.length} file(s) selected`;

    container.innerHTML = attachedFiles.map((file, index) => `
        <div class="attached-file">
            <span>📄 ${file.name}</span>
            <button class="remove-attachment" onclick="removeAttachment(${index})">&times;</button>
        </div>
    `).join('');
}

function removeAttachment(index) {
    attachedFiles.splice(index, 1);
    updateAttachedFilesList();
}

async function loadTickets() {
    try {
        const response = await fetch('/api/tickets');
        const data = await response.json();

        currentTickets = data.tickets.map(t => ({
            ...t,
            row_number: t.id
        }));

        // Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Update Last Refreshed Timestamp on Load - start
        lastRefreshedTimestamp = Date.now();
        updateRefreshTooltip();
        // Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Update Last Refreshed Timestamp on Load - end

        displayTickets();
    } catch (error) {
        console.error('Error loading tickets:', error);
    }
}

function toggleFilterDropdown(type) {
    const dropdownId = type === 'assignment' ? 'assignmentFilterContent' : 'customerFilterContent';
    const btnId = type === 'assignment' ? 'assignmentFilterBtn' : 'customerFilterBtn';
    const otherDropdownId = type === 'assignment' ? 'customerFilterContent' : 'assignmentFilterContent';
    const otherBtnId = type === 'assignment' ? 'customerFilterBtn' : 'assignmentFilterBtn';

    const dropdown = document.getElementById(dropdownId);
    const btn = document.getElementById(btnId);
    const otherDropdown = document.getElementById(otherDropdownId);
    const otherBtn = document.getElementById(otherBtnId);

    otherDropdown.classList.remove('show');
    otherBtn.classList.remove('open');

    dropdown.classList.toggle('show');
    btn.classList.toggle('open');
}

function populateAssignmentFilter() {
    const assignments = new Set();
    currentTickets.forEach(ticket => {
        const assignment = ticket.assigned_to || 'Unassigned';
        assignments.add(assignment);
    });

    const content = document.getElementById('assignmentFilterContent');
    if (!content) return;

    let html = `<div class="filter-dropdown-item ${currentAssignmentFilter === null ? 'selected' : ''}" onclick="setAssignmentFilter(null)">All Assignments</div>`;

    Array.from(assignments).sort().forEach(assignment => {
        const isSelected = currentAssignmentFilter === assignment ? 'selected' : '';
        html += `<div class="filter-dropdown-item ${isSelected}" onclick="setAssignmentFilter('${escapeHtml(assignment)}')">${escapeHtml(assignment)}</div>`;
    });

    content.innerHTML = html;
}

function populateCustomerFilter() {
    const customers = new Set();
    currentTickets.forEach(ticket => {
        if (ticket.customer_email) {
            const domain = ticket.customer_email.trim().split('@').pop();
            customers.add(domain);
        }
    });

    const content = document.getElementById('customerFilterContent');
    if (!content) return;

    let html = `<div class="filter-dropdown-item ${currentCustomerFilter === null ? 'selected' : ''}" onclick="setCustomerFilter(null)">All Customers</div>`;

    Array.from(customers).sort().forEach(customer => {
        const isSelected = currentCustomerFilter === customer ? 'selected' : '';
        html += `<div class="filter-dropdown-item ${isSelected}" onclick="setCustomerFilter('${escapeHtml(customer)}')">${escapeHtml(customer)}</div>`;
    });

    content.innerHTML = html;
}

function setAssignmentFilter(assignment) {
    currentAssignmentFilter = assignment;
    const btn = document.getElementById('assignmentFilterBtn');
    const text = document.getElementById('assignmentFilterText');
    if (assignment) {
        text.textContent = `👤 ${assignment}`;
        btn.classList.add('has-filter');
    } else {
        text.textContent = '👤 Assignment';
        btn.classList.remove('has-filter');
    }
    document.getElementById('assignmentFilterContent').classList.remove('show');
    btn.classList.remove('open');
    displayTickets();
}

function setCustomerFilter(customer) {
    currentCustomerFilter = customer;
    const btn = document.getElementById('customerFilterBtn');
    const text = document.getElementById('customerFilterText');
    if (customer) {
        const shortEmail = customer.length > 20 ? customer.substring(0, 20) + '...' : customer;
        text.textContent = `📧 ${shortEmail}`;
        btn.classList.add('has-filter');
    } else {
        text.textContent = '📧 Customer';
        btn.classList.remove('has-filter');
    }
    document.getElementById('customerFilterContent').classList.remove('show');
    btn.classList.remove('open');
    displayTickets();
}

function normalizeStatus(status) {
    if (!status) return 'Open';
    const statusLower = status.toLowerCase();

    if (statusLower === 'resolved' || statusLower === 'completed' || statusLower === 'closed') {
        return 'Closed';
    } else if (statusLower === 'review' || statusLower === 'pending review') {
        return 'Review';
    } else if (statusLower === 'ignore') {
        return 'Ignore';
    } else if (statusLower === 'pending' || statusLower === 'open' || statusLower === 'in progress') {
        return 'Open';
    }
    return 'Open'; // Default
}

function updateStats(stats) {
    const totalEl = document.getElementById('stat-total');
    const openEl = document.getElementById('stat-open');
    const closedEl = document.getElementById('stat-closed');
    if (totalEl) totalEl.textContent = stats.total || 0;
    if (openEl) openEl.textContent = stats.open || 0;
    if (closedEl) closedEl.textContent = stats.closed || 0;
}

function calculateWorkingHours(startDate, endDate) {
    let hours = 0;
    let current = new Date(startDate);
    const end = new Date(endDate);

    while (current < end) {
        const dayOfWeek = current.getDay();
        if (dayOfWeek !== 0 && dayOfWeek !== 6) {
            const nextHour = new Date(current.getTime() + 60 * 60 * 1000);
            if (nextHour <= end) {
                hours++;
            } else {
                hours += (end - current) / (1000 * 60 * 60);
                break;
            }
        }
        current = new Date(current.getTime() + 60 * 60 * 1000);
    }
    return hours;
}

function isUnanswered24h(ticket) {
    if (normalizeStatus(ticket.status) !== 'Open') return false;
    if (!ticket.messages || ticket.messages.length === 0) return false;
    const sortedMessages = [...ticket.messages].sort((a, b) =>
        new Date(b.timestamp) - new Date(a.timestamp)
    );
    const latestMessage = sortedMessages[0];
    const isFromCustomer = latestMessage.is_internal === false;
    if (!isFromCustomer) return false;
    const hasInternalReplyAfter = sortedMessages.some(msg => {
        const isInternal = msg.is_internal === true;
        return isInternal && new Date(msg.timestamp) > new Date(latestMessage.timestamp);
    });
    if (hasInternalReplyAfter) return false;
    const now = new Date();
    const messageTime = new Date(latestMessage.timestamp);
    const workingHours = calculateWorkingHours(messageTime, now);
    return workingHours > 24;
}

function isLongRunningOpen(ticket) {
    if (normalizeStatus(ticket.status) !== 'Open') return false;
    const now = new Date();
    const created = new Date(ticket.created_at);
    const workingHours = calculateWorkingHours(created, now);
    return workingHours > 48;
}

function displayTickets() {
    const container = document.getElementById('tickets');
    const searchInput = document.getElementById('search');
    const searchQuery = searchInput ? searchInput.value.toLowerCase() : '';

    const currentState = `${currentVisibility}|${currentFilter}|${currentAssignmentFilter}|${currentCustomerFilter}|${searchQuery}|${currentTimeRange}|${customFromDate}|${customToDate}`;
    if (lastDisplayState !== currentState) {
        currentPage = 1;
        lastDisplayState = currentState;
    }

    let processedTickets = currentTickets.map(ticket => {
        let latestMessageTime = ticket.last_updated || ticket.last_message_at || ticket.created_at;
        if (ticket.messages && ticket.messages.length > 0) {
            const messageTimestamps = ticket.messages.map(m => new Date(m.timestamp).getTime());
            latestMessageTime = new Date(Math.max(...messageTimestamps));
        }

        return {
            ...ticket,
            latestActivity: new Date(latestMessageTime)
        };
    });

    let startDate = null;
    let endDate = null;
    const now = new Date();

    if (currentTimeRange === 'today') {
        startDate = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        endDate = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59, 999);
    } else if (currentTimeRange === 'week') {
        const day = now.getDay();
        const diff = now.getDate() - day + (day === 0 ? -6 : 1); // start of week (Monday)
        startDate = new Date(now.getFullYear(), now.getMonth(), diff, 0, 0, 0, 0);
        endDate = new Date(now.getFullYear(), now.getMonth(), diff + 6, 23, 59, 59, 999);
    } else if (currentTimeRange === 'custom' && customFromDate && customToDate) {
        const parseDMY = (str) => {
            if (!str) return null;
            const parts = str.split('-');
            if (parts.length !== 3) return null;
            return new Date(parts[2], parts[1] - 1, parts[0]);
        };
        startDate = parseDMY(customFromDate);
        if (startDate) startDate.setHours(0,0,0,0);
        endDate = parseDMY(customToDate);
        if (endDate) endDate.setHours(23,59,59,999);
    }

    const filterByDateRange = (t) => {
        if (!startDate || !endDate) return true;
        const created = new Date(t.created_at);
        const active = t.latestActivity;
        return (created >= startDate && created <= endDate) || (active >= startDate && active <= endDate);
    };

    let filteredTickets = processedTickets;

    if (currentVisibility === 'mine') {
        filteredTickets = filteredTickets.filter(t => t.assigned_to === currentUsername);
    }

    if (typeof currentFilter !== 'undefined' && currentFilter !== 'all') {
        filteredTickets = filteredTickets.filter(t => {
            const status = normalizeStatus(t.status);
            if (currentFilter === 'Open') return status === 'Open' || status === 'Review';
            if (currentFilter === 'Closed') return status === 'Closed' || status === 'Ignore';
            return status === currentFilter;
        });
    }

    if (typeof currentAssignmentFilter !== 'undefined' && currentAssignmentFilter !== null) {
        filteredTickets = filteredTickets.filter(t => (t.assigned_to || 'Unassigned') === currentAssignmentFilter);
    }

    if (typeof currentCustomerFilter !== 'undefined' && currentCustomerFilter !== null) {
        filteredTickets = filteredTickets.filter(t => {
            if (!t.customer_email) return false;
            const ticketDomain = t.customer_email.trim().split('@').pop();
            return ticketDomain === currentCustomerFilter;
        });
    }

    if (searchQuery) {
        filteredTickets = filteredTickets.filter(t =>
            (t.customer_email && t.customer_email.toLowerCase().includes(searchQuery)) ||
            (t.subject && t.subject.toLowerCase().includes(searchQuery)) ||
            (t.ticket_id && t.ticket_id.toLowerCase().includes(searchQuery))
        );
    }

    filteredTickets = filteredTickets.filter(filterByDateRange);

    let statsBase = processedTickets;
    if (currentVisibility === 'mine') {
        statsBase = statsBase.filter(t => t.assigned_to === currentUsername);
    }
    if (typeof currentAssignmentFilter !== 'undefined' && currentAssignmentFilter !== null) {
        statsBase = statsBase.filter(t => (t.assigned_to || 'Unassigned') === currentAssignmentFilter);
    }
    if (typeof currentCustomerFilter !== 'undefined' && currentCustomerFilter !== null) {
        statsBase = statsBase.filter(t => {
            if (!t.customer_email) return false;
            return t.customer_email.trim().split('@').pop() === currentCustomerFilter;
        });
    }
    if (searchQuery) {
        statsBase = statsBase.filter(t =>
            (t.customer_email && t.customer_email.toLowerCase().includes(searchQuery)) ||
            (t.subject && t.subject.toLowerCase().includes(searchQuery)) ||
            (t.ticket_id && t.ticket_id.toLowerCase().includes(searchQuery))
        );
    }
    statsBase = statsBase.filter(filterByDateRange);

    const dynamicStats = {
        total: statsBase.filter(t => normalizeStatus(t.status) !== 'Ignore').length,
        open: statsBase.filter(t => {
            const s = normalizeStatus(t.status);
            return s === 'Open' || s === 'Review';
        }).length,
        closed: statsBase.filter(t => {
            const s = normalizeStatus(t.status);
            return s === 'Closed' || s === 'Ignore';
        }).length
    };
    updateStats(dynamicStats);

    if (typeof populateAssignmentFilter === 'function') populateAssignmentFilter();
    if (typeof populateCustomerFilter === 'function') populateCustomerFilter();

    filteredTickets.sort((a, b) => b.latestActivity - a.latestActivity);

    if (filteredTickets.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">🔭</div>
                <h3>No tickets found</h3>
                <p>Try adjusting your filters</p>
            </div>
        `;
        return;
    }

    const totalRecords = filteredTickets.length;
    const totalPages = Math.max(1, Math.ceil(totalRecords / pageSize));
    
    if (currentPage > totalPages) {
        currentPage = totalPages;
    }
    
    const startIndex = (currentPage - 1) * pageSize;
    const endIndex = startIndex + pageSize;
    const ticketsToRender = filteredTickets.slice(startIndex, endIndex);

    const ticketCards = ticketsToRender.map(ticket => {
        const ticketId = escapeHtml(ticket.ticket_id || 'NO-ID');
        const displayId = escapeHtml(ticket.display_id || truncateTicketId(ticket.ticket_id || 'NO-ID'));
        const rowId = ticket.id;
        const assignedTo = escapeHtml(ticket.assigned_to || 'Unassigned');
        const subject = escapeHtml(ticket.subject || 'No Subject');
        const customerEmail = escapeHtml(ticket.customer_email || 'Unknown');
        const timestamp = formatDate(ticket.last_message_at || ticket.created_at);

        const status = normalizeStatus(ticket.status);
        const statusClass = status.toLowerCase();

        const showUnansweredBadge = false;
        const applyLongRunningTint = false;

        let slaCardClass = '';

        const isReopened = ticket.reopened === true;
        if (isReopened) {
            slaCardClass += ' reopened-tint';
        }

        const hasUnreadResponse = (ticket.has_unread_response === true || ticket.has_unread_response === 1) && status === 'Open';
        if (hasUnreadResponse) {
            slaCardClass += ' unread-tint';
        }

        const unansweredBadgeHtml = showUnansweredBadge ?
            '<span class="sla-badge-unanswered">⏰ Unanswered 24h+</span>' : '';

        const importantBadgeHtml = isReopened ?
            '<span class="important-badge">🚩 Reopened</span>' : '';

        const unreadBadgeHtml = hasUnreadResponse ?
            '<span class="unread-badge" title="New customer response unread">⭐ New</span>' : '';

        const isLocked = ticket.locked_by && ticket.locked_by !== null;
        const lockClass = isLocked ? 'locked-by-other' : '';
        const lockBadge = isLocked ? `<span style="font-size:0.8rem; margin-left:5px">🔒 ${escapeHtml(ticket.locked_by)}</span>` : '';

        return `
        <div class="ticket-card ${lockClass} ${slaCardClass}" data-ticket-id="${ticketId}" onclick="openTicket('${ticketId}', ${rowId})">
            <div class="btn-timeline" title="View Ticket Timeline (SLA)" onclick="event.stopPropagation(); showTimeline('${ticketId}', ${JSON.stringify(subject).replace(/"/g, '&quot;')}, '${displayId}')">🕒</div>
            <div class="ticket-header">
                <div class="ticket-id-row">
                    <span class="ticket-id" title="${ticketId}">${displayId}</span>
                    ${lockBadge}
                    ${unansweredBadgeHtml}
                    ${importantBadgeHtml}
                    ${unreadBadgeHtml}
                </div>
                <div style="display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center;">
                    <span class="ticket-status status-${statusClass}">${status}</span>
                    <span class="assignment-badge ${assignedTo === 'Unassigned' ? 'unassigned' : ''}">👤 ${assignedTo}</span>
                </div>
            </div>
            <div class="ticket-subject">${subject}</div>
            <div class="ticket-meta">
                <span>📧 ${customerEmail}</span>
                <span>📅 Last Replied: ${timestamp}</span>
            </div>
        </div>
        `;
    }).join('');

    let paginationHtml = '';
    if (totalPages > 1) {
        paginationHtml = `
            <div class="pagination-controls" style="display: flex; justify-content: space-between; align-items: center; width: 100%; padding: 15px 20px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 8px; margin-top: 20px;">
                <div style="color: var(--text-secondary); font-size: 0.9rem;">
                    Showing <strong>${startIndex + 1}-${Math.min(endIndex, totalRecords)}</strong> of <strong>${totalRecords}</strong> tickets
                </div>
                <div style="display: flex; gap: 10px; align-items: center;">
                    <button class="btn btn-sm btn-outline-secondary" onclick="currentPage--; displayTickets();" ${currentPage === 1 ? 'disabled' : ''} style="color: var(--text-primary); border-color: var(--border-color);">Previous</button>
                    <span style="font-weight: 500; font-size: 0.95rem; color: var(--text-primary);">Page ${currentPage} of ${totalPages}</span>
                    <button class="btn btn-sm btn-outline-secondary" onclick="currentPage++; displayTickets();" ${currentPage === totalPages ? 'disabled' : ''} style="color: var(--text-primary); border-color: var(--border-color);">Next</button>
                </div>
            </div>
        `;
    } else if (totalRecords > 0) {
        paginationHtml = `
            <div style="text-align: center; padding: 10px; color: var(--text-secondary); font-size: 0.85rem;">
                Showing all <strong>${totalRecords}</strong> tickets
            </div>
        `;
    }

    container.innerHTML = ticketCards + paginationHtml;
}

function renderTicketSkeleton() {
    // Skeleton for individual info items
    const fields = ['modal-ticket-id', 'modal-email', 'modal-date', 'modal-subject'];
    fields.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = '<div class="skeleton skeleton-text" style="width: 150px; display: inline-block; vertical-align: middle;"></div>';
    });

    // Skeleton for message thread
    const threadContainer = document.getElementById('emailChainContainer');
    if (threadContainer) {
        threadContainer.innerHTML = `
            <div style="display: flex; flex-direction: column; gap: 1rem; width: 100%;">
                <div class="skeleton skeleton-bubble customer"></div>
                <div class="skeleton skeleton-bubble staff" style="align-self: flex-end;"></div>
                <div class="skeleton skeleton-bubble customer"></div>
            </div>
        `;
    }

    // Skeleton for draft editor (optional, but good for UX)
    // if (quillEditor) {
    //     quillEditor.root.innerHTML = '<div class="skeleton skeleton-text"></div><div class="skeleton skeleton-text"></div><div class="skeleton skeleton-text" style="width: 60%;"></div>';
    // }
}

async function openTicket(ticketId, rowNumber) {
    currentTicketRow = rowNumber;
    currentTicketIdStr = ticketId;

    // Show modal and skeleton immediately
    document.getElementById('ticketModal').classList.add('active');
    renderTicketSkeleton();

    let ticket = null;
    try {
        const res = await fetch(`/api/get_ticket/${encodeURIComponent(ticketId)}`);
        if (res.ok) {
            ticket = await res.json();
        }
    } catch (err) {
        console.error("Error fetching ticket details:", err);
    }

    if (!ticket) {
        ticket = currentTickets.find(t => t.id === rowNumber || t.ticket_id === ticketId);
    }

    if (!ticket) {
        closeModal();
        return;
    }

    currentTicketStatus = normalizeStatus(ticket.status);

    initializeEditor();
    attachedFiles = [];
    updateAttachedFilesList();

    const rightPanel = document.getElementById('rightPanel');
    if (rightPanel) {
        isRightPanelCollapsed = true;
        rightPanel.classList.add('collapsed');
    }

    const emailToEl = document.getElementById('emailTo');
    if (emailToEl) {
        if (testMode) {
            emailToEl.value = testEmail;
            emailToEl.readOnly = true;
            emailToEl.classList.add('test-mode-input');
            emailToEl.title = "Email redirected to test receiver in Test Mode";
        } else {
            emailToEl.value = ticket.customer_email || '';
            emailToEl.readOnly = false;
            emailToEl.classList.remove('test-mode-input');
            emailToEl.style.backgroundColor = "";
            emailToEl.style.cursor = "";
            emailToEl.title = "";
        }
    }
    document.getElementById('modal-subject').textContent = ticket.subject || 'No Subject';
    document.getElementById('modal-ticket-id').textContent = ticket.display_id || truncateTicketId(ticket.ticket_id || 'NO-ID');
    document.getElementById('modal-ticket-id').title = ticket.ticket_id || 'NO-ID';
    document.getElementById('modal-email').textContent = ticket.customer_email || 'Unknown';
    document.getElementById('modal-date').textContent = formatDate(ticket.last_updated);
    document.getElementById('modal-assignment').value = ticket.assigned_to || '';
    document.body.classList.add('modal-open');

    // Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Populate Tiptap editor and load DevOps items - start
    if (!tiptapEditor && typeof initTiptapEditor === 'function') {
        initTiptapEditor();
    }
    if (tiptapEditor) {
        if (ticket.ai_draft) {
            const sanitizedDraft = DOMPurify.sanitize(ticket.ai_draft, {
                ADD_TAGS: ['p', 'br', 'strong', 'em', 'u', 's', 'a', 'ul', 'ol', 'li', 'blockquote', 'img'],
                ADD_ATTR: ['src', 'alt', 'title', 'href', 'target', 'data-cid', 'class']
            });
            tiptapEditor.commands.setContent(sanitizedDraft);
        } else {
            tiptapEditor.commands.setContent('');
        }
    }

    // Reset email outbound attachments
    emailPendingAttachments = [];
    renderEmailAttachmentPreviews();

    // Load linked Azure DevOps work items for this ticket
    const devopsMenu = document.getElementById('devopsDropdownMenu');
    if (devopsMenu) devopsMenu.classList.add('d-none');
    loadLinkedDevOpsItems(ticketId);
    // Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Populate Tiptap editor and load DevOps items - end

    const statusSelect = document.getElementById('modal-status');
    if (statusSelect) {
        statusSelect.value = currentTicketStatus;
    }

    const lockStatus = document.getElementById('lockStatus');
    const btnSave = document.getElementById('btnSave');
    const btnSend = document.getElementById('btnSend');

    if (ticket.locked_by) {
        isTicketLocked = true;
        if (lockStatus) {
            lockStatus.textContent = `🔒 Locked by ${ticket.locked_by}`;
            lockStatus.classList.add('active');
        }
        if (tiptapEditor) tiptapEditor.setEditable(false);
        if (btnSave) btnSave.disabled = true;
        if (btnSend) btnSend.disabled = true;
        if (statusSelect) statusSelect.disabled = true;
    } else {
        isTicketLocked = false;
        if (lockStatus) {
            lockStatus.classList.remove('active');
        }
        if (tiptapEditor) tiptapEditor.setEditable(true);
        if (btnSave) btnSave.disabled = false;
        if (btnSend) btnSend.disabled = false;
        if (statusSelect) statusSelect.disabled = false;

        if (socket && socket.connected) {
            socket.emit('lock_ticket', { ticket_id: ticketId });
        }
    }

    const emailChainContainer = document.getElementById('emailChainContainer');

    try {
        const res = await fetch(`/api/ticket_messages/${ticketId}`);
        if (!res.ok) throw new Error("Failed to fetch messages");
        const data = await res.json();
        const messages = data.messages || [];

        renderThread(messages);
        populateEmailTicketChips(messages);

        const emailCC = document.getElementById('emailCC');
        const emailBCC = document.getElementById('emailBCC');
        const ccField = document.getElementById('ccField');
        const bccField = document.getElementById('bccField');

        if (emailCC && emailBCC && ccField && bccField) {
            if (testMode) {
                emailCC.value = testCC || '';
                emailBCC.value = '';
                if (testCC) ccField.classList.remove('hidden');
                else ccField.classList.add('hidden');
                bccField.classList.add('hidden');
            } else if (messages.length > 0) {
                const sorted = [...messages].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
                const latest = sorted[0];
                emailCC.value = latest.cc || '';
                emailBCC.value = latest.bcc || '';

                if (emailCC.value) ccField.classList.remove('hidden');
                else ccField.classList.add('hidden');

                if (emailBCC.value) bccField.classList.remove('hidden');
                else bccField.classList.add('hidden');
            } else {
                emailCC.value = '';
                emailBCC.value = '';
                ccField.classList.add('hidden');
                bccField.classList.add('hidden');
            }
        }

    } catch (err) {
        console.error("Error fetching messages:", err);
        emailChainContainer.innerHTML = '<p style="color:red; padding:10px;">Error loading thread.</p>';
    }
}

async function changeTicketStatus() {
    if (isTicketLocked) return;
    const statusSelect = document.getElementById('modal-status');
    if (!statusSelect) return;
    const newStatus = statusSelect.value;
    
    if (!confirm(`Are you sure you want to change ticket status to ${newStatus}?`)) {
        statusSelect.value = currentTicketStatus;
        return;
    }

    try {
        const response = await fetch('/api/toggle_ticket_status', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                row_number: currentTicketRow,
                ticket_id: currentTicketIdStr,
                status: newStatus
            })
        });

        if (response.ok) {
            const data = await response.json();
            currentTicketStatus = data.new_status;
            statusSelect.value = currentTicketStatus;

            if (currentTicketStatus === 'Closed') {
                closeModal();
            }

            loadTickets();
        } else {
            alert('Failed to update ticket status.');
            statusSelect.value = currentTicketStatus;
        }
    } catch (error) {
        console.error('Error changing status:', error);
        alert('Error updating ticket status.');
        statusSelect.value = currentTicketStatus;
    }
}

function escapeHtml(text) {
    if (!text) return "";
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Fix CID image replacement regex and content_type suffix - start
function processCidImages(body, cidMap) {
    if (!body) return '';
    let newBody = body;

    if (!cidMap || Object.keys(cidMap).length === 0) return body;

    const escapeRegExp = (string) => string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

    // Sort by longest key first to prevent prefix matching bugs (e.g. image001.png matching before image001.png@01DD...)
    const sortedCids = Object.keys(cidMap).sort((a, b) => b.length - a.length);

    sortedCids.forEach(cid => {
        const att = cidMap[cid];
        const rawCt = (att.ct || '').split('@')[0].trim();
        const viewUrl = `${window.location.origin}/api/view_attachment?message_id=${encodeURIComponent(att.msgId)}&attachment_id=${encodeURIComponent(att.id)}&filename=${encodeURIComponent(att.name || 'image')}&content_type=${encodeURIComponent(rawCt || 'image/png')}`;

        const escapedCid = escapeRegExp(cid);
        const encodedCid = escapeRegExp(cid.replace(/@/g, '%40'));

        // Match cid:cid, cid:<cid>, cid:cid@domain, cid:cid%40domain
        const pattern = new RegExp(`cid:<?(?:${escapedCid}|${encodedCid})(?:@[^"'>\\s]*)?>?`, 'gi');
        newBody = newBody.replace(pattern, viewUrl);
    });

    return newBody;
}
// Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Fix CID image replacement regex and content_type suffix - end

// Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Render Gmail style attachment tiles with lazy thumbnails - start
function renderAttachments(attachments, messageId) {
    let attachmentsHtml = '';
    let attList = [];
    try {
        if (attachments) {
            attList = typeof attachments === 'string' ? JSON.parse(attachments) : attachments;
        }
    } catch (e) { console.error('Attachment parse error', e); }

    if (attList.length > 0) {
        attachmentsHtml = '<div class="message-attachments" style="margin-top: 1rem; padding-top: 0.75rem; border-top: 1px solid var(--border-color);">';
        attachmentsHtml += '<div style="font-weight: 600; margin-bottom: 0.5rem; font-size: 0.9rem; color: var(--text-secondary);">Attachments:</div>';
        attachmentsHtml += '<div class="attachments-grid">';

        attList.forEach((att, idx) => {
            const attName = escapeHtml(att.name || 'file');
            const isImage = (att.content_type && att.content_type.startsWith('image/')) || /\.(jpg|jpeg|png|gif|webp|bmp)$/i.test(att.name || '');
            const isPDF = att.content_type === 'application/pdf' || (att.name && att.name.toLowerCase().endsWith('.pdf'));
            const isTable = (att.name && /\.(xlsx|xls|csv|tsv)$/i.test(att.name));
            const isDoc = (att.name && /\.(docx|doc|txt)$/i.test(att.name));
            const isPreviewable = isImage || isPDF;

            let previewContent = '';
            if (isImage) {
                const thumbUrl = `/api/view_attachment?message_id=${encodeURIComponent(messageId || '')}&attachment_id=${encodeURIComponent(att.id)}&filename=${encodeURIComponent(att.name || 'image')}&content_type=${encodeURIComponent(att.content_type || '')}&thumbnail=true`;
                previewContent = `
                    <img loading="lazy" src="${thumbUrl}" class="attachment-tile-img" alt="${attName}" onerror="this.style.display='none'; if(this.nextElementSibling) this.nextElementSibling.style.display='flex';" />
                    <div class="attachment-tile-icon" style="display: none;">🖼️</div>
                `;
            } else if (isPDF) {
                previewContent = '<div class="attachment-tile-icon">📕</div>';
            } else if (isTable) {
                previewContent = '<div class="attachment-tile-icon">📊</div>';
            } else if (isDoc) {
                previewContent = '<div class="attachment-tile-icon">📄</div>';
            } else {
                previewContent = '<div class="attachment-tile-icon">📁</div>';
            }

            attachmentsHtml += `
                <div class="attachment-tile" 
                     ${isPreviewable ? `data-att-id="${escapeHtml(att.id)}" data-msg-id="${escapeHtml(messageId || '')}" data-filename="${attName}" data-type="${escapeHtml(att.content_type || '')}" onclick="event.stopPropagation(); handlePreviewClick(this)" title="Click to preview ${attName}"` : `title="${attName}"`}>
                    <div class="attachment-tile-preview">
                        ${previewContent}
                        ${isPreviewable ? `
                        <div class="attachment-tile-overlay">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                            <span>Preview</span>
                        </div>` : ''}
                    </div>
                    <div class="attachment-tile-info">
                        <div class="attachment-tile-name" title="${attName}">${attName}</div>
                        <div class="attachment-tile-footer">
                            <span>${formatFileSize(att.size)}</span>
                            <button type="button" 
                                    class="attachment-tile-download-btn" 
                                    title="Download ${attName}" 
                                    onclick="event.stopPropagation(); downloadAttachment('${escapeHtml(att.id)}', '${escapeHtml(messageId || '')}', '${attName}')">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
                            </button>
                        </div>
                    </div>
                </div>
            `;
        });

        attachmentsHtml += '</div></div>';
    }
    return attachmentsHtml;
}
// Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Render Gmail style attachment tiles with lazy thumbnails - end

function toggleMessage(card, e) {
    if (window.getSelection().toString()) return;
    if (e.target.closest('.message-body')) {
        return;
    }

    if (e.target.closest('a') || e.target.closest('button') || e.target.closest('.quoted-text-btn')) {
        return;
    }
    const body = card.querySelector('.message-body');
    const isHiding = body.classList.contains('visible');

    if (isHiding) {
        body.classList.remove('visible');
        card.classList.remove('expanded');
    } else {
        body.classList.add('visible');
        card.classList.add('expanded');

        const iframes = body.querySelectorAll('iframe');
        iframes.forEach(ifr => {
            if (ifr.contentWindow) {
                ifr.contentWindow.postMessage('trigger-resize', '*');
            }
        });
    }
}

// Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Upgraded Email Chain UI with Avatars and Time-Gap Dividers - start
function getSenderInitials(sender) {
    if (!sender) return '?';
    const clean = sender.replace(/<.*?>/g, '').replace(/["']/g, '').trim();
    const parts = clean.split(/[\s.@_]+/).filter(p => p.length > 0 && !['com', 'net', 'org', 'io', 'in'].includes(p.toLowerCase()));
    if (parts.length >= 2) {
        return (parts[0][0] + parts[1][0]).toUpperCase();
    }
    return (clean[0] || '?').toUpperCase();
}

function renderThread(messages) {
    const container = document.getElementById('emailChainContainer');
    if (!messages || messages.length === 0) {
        container.innerHTML = '<div style="text-align:center; padding: 2rem; color: var(--text-secondary);"><span style="font-size: 2rem; display:block; margin-bottom: 0.5rem;">📭</span>No conversation history found.</div>';
        return;
    }

    const sortedMessages = [...messages].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
    container.innerHTML = '';

    const globalCidMap = {};
    messages.forEach(m => {
        try {
            const atts = typeof m.attachments === 'string' ? JSON.parse(m.attachments) : (m.attachments || []);
            atts.forEach(a => {
                if (a.content_id) {
                    const cleanCid = a.content_id.replace(/^<|>$/g, '').trim();
                    globalCidMap[cleanCid] = { id: a.id, msgId: m.message_id, name: a.name, ct: a.content_type };
                }
                if (a.name) {
                    const cleanName = a.name.trim();
                    if (!globalCidMap[cleanName]) {
                        globalCidMap[cleanName] = { id: a.id, msgId: m.message_id, name: a.name, ct: a.content_type };
                    }
                }
            });
        } catch (e) { }
    });

    sortedMessages.forEach((msg, index) => {
        // Calculate time gap with previous message in timeline (which is index + 1 in descending list)
        if (index > 0) {
            const prevMsg = sortedMessages[index - 1];
            const prevTime = new Date(prevMsg.timestamp).getTime();
            const currTime = new Date(msg.timestamp).getTime();
            const diffMs = prevTime - currTime;
            if (diffMs > 3600000) { // > 1 hour gap
                const hours = Math.round(diffMs / 3600000);
                const gapText = hours < 24 ? `⏱️ ${hours} hours later` : `📅 ${Math.round(hours / 24)} days later`;
                const divider = document.createElement('div');
                divider.className = 'time-gap-divider';
                divider.innerHTML = `<span class="time-gap-badge">${gapText}</span>`;
                container.appendChild(divider);
            }
        }

        const messageCard = document.createElement('div');
        messageCard.className = `message-card ${msg.is_internal ? 'internal-note' : 'customer-message'}`;
        if (index === 0) messageCard.classList.add('expanded');

        let rawSender = (msg.sender || '').trim();
        const activeTicket = currentTickets.find(t => t.id === currentTicketRow || t.ticket_id === currentTicketIdStr);
        if (!rawSender || rawSender.toLowerCase() === 'unknown') {
            if (msg.is_internal) {
                rawSender = globalConfig.USER_EMAIL || 'support@greenwaresolutions.com';
            } else if (activeTicket && activeTicket.customer_email) {
                rawSender = activeTicket.customer_email;
            } else {
                rawSender = 'Customer';
            }
        }
        const sender = escapeHtml(rawSender);
        const initials = getSenderInitials(rawSender);
        const timestamp = formatDate(msg.timestamp);
        const processedBody = processCidImages(msg.body_html || msg.body || msg.body_text || '', globalCidMap);
        const rawBody = (processedBody || '').trim();

        let displayBody = rawBody;
        let threadSplit = -1;

        const splitters = [/From:\s.+/i, /Sent:\s.+/i, /________________________________/];
        for (let reg of splitters) {
            const match = displayBody.match(reg);
            if (match && (threadSplit === -1 || match.index < threadSplit)) {
                threadSplit = match.index;
            }
        }

        let mainContent = displayBody;
        let threadContent = '';
        if (threadSplit !== -1) {
            mainContent = displayBody.substring(0, threadSplit).trim();
            threadContent = displayBody.substring(threadSplit).trim();
        }

        const sanitizeConfig = {
            ADD_TAGS: ['style', 'meta', 'link', 'xml', 'base', 'head', 'body', 'html'],
            ADD_ATTR: ['src', 'alt', 'title', 'target', 'style', 'bgcolor', 'valign', 'align', 'cellpadding', 'cellspacing', 'border', 'width', 'height', 'class', 'id', 'font', 'color', 'background', 'hspace', 'vspace'],
            WHOLE_DOCUMENT: true,
            FORCE_BODY: true,
            ALLOWED_URI_REGEXP: /^(?:(?:(?:f|ht)tps?|mailto|tel|callto|cid|data|xmpp):|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i
        };

        const sanitizedMain = DOMPurify.sanitize(mainContent, sanitizeConfig);
        const sanitizedThread = threadContent ? DOMPurify.sanitize(threadContent, sanitizeConfig) : null;

        const ccList = msg.cc ? escapeHtml(msg.cc) : '';
        const bccList = msg.bcc ? escapeHtml(msg.bcc) : '';

        let toEmail = msg.to_email ? escapeHtml(msg.to_email) : '';
        if (!toEmail) {
            const activeTicket = currentTickets.find(t => t.id === currentTicketRow || t.ticket_id === currentTicketIdStr);
            if (msg.is_internal) {
                toEmail = activeTicket ? escapeHtml(activeTicket.customer_email || 'Customer') : 'Customer';
            } else {
                toEmail = escapeHtml(globalConfig.USER_EMAIL || 'Support Mailbox');
            }
        }

        messageCard.innerHTML = `
            <div class="message-header">
                <div class="msg-header-top">
                    <div class="msg-speaker-info">
                        <div class="sender-avatar ${msg.is_internal ? 'staff' : 'customer'}">${initials}</div>
                        <div>
                            <span class="badge ${msg.is_internal ? 'bg-primary' : 'bg-warning text-dark'}" style="font-size: 0.75rem;">
                                ${msg.is_internal ? '🛡️ Staff Note / Reply' : '👤 Customer Message'}
                            </span>
                        </div>
                    </div>
                    <span class="msg-time-badge"><i class="far fa-clock"></i> ${timestamp}</span>
                </div>
                <div class="msg-header-cols">
                    <div class="msg-header-col">
                        <span class="msg-header-label">From</span>
                        <span class="msg-header-val" title="${sender}">${sender}</span>
                    </div>
                    <div class="msg-header-col">
                        <span class="msg-header-label">To</span>
                        <span class="msg-header-val" title="${toEmail}">${toEmail}</span>
                    </div>
                    <div class="msg-header-col">
                        <span class="msg-header-label">CC ${bccList ? '(+BCC)' : ''}</span>
                        <span class="msg-header-val" title="${ccList || 'None'}${bccList ? ' | BCC: ' + bccList : ''}">${ccList || '<span style="opacity:0.4; font-weight:normal;">—</span>'}</span>
                    </div>
                </div>
            </div>
            <div class="message-body ${index === 0 ? 'visible' : ''}">
                <div class="email-iframe-container">
                    <iframe id="iframe-${msg.message_id || index}" sandbox="allow-same-origin allow-scripts allow-popups allow-popups-to-escape-sandbox" scrolling="no"></iframe>
                </div>
                
                ${sanitizedThread ? `
                <div class="thread-toggle-container" style="padding: 0 16px;">
                    <button type="button" class="quoted-text-btn" onclick="event.stopPropagation(); const container = this.nextElementSibling; const isHiding = container.style.display === 'none'; container.style.display = isHiding ? 'block' : 'none'; this.innerHTML = isHiding ? '📋 Hide Quoted Thread ▲' : '📋 Show Quoted Thread ▼'; if (isHiding) { const ifr = container.querySelector('iframe'); ifr.contentWindow.postMessage('trigger-resize', '*'); }">📋 Show Quoted Thread ▼</button>
                    <div class="email-iframe-container" style="display: none; border-top: none; border-radius: 0 0 8px 8px; opacity: 0.9;">
                        <iframe id="iframe-thread-${msg.message_id || index}" sandbox="allow-same-origin allow-scripts allow-popups allow-popups-to-escape-sandbox" scrolling="no"></iframe>
                    </div>
                </div>
                ` : ''}

                <div style="padding: 0 16px 16px;">
                    ${renderAttachments(msg.attachments, msg.message_id)}
                </div>
            </div>
        `;
// Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Upgraded Email Chain UI with Avatars and Time-Gap Dividers - end

        messageCard.addEventListener('click', function (e) {
            toggleMessage(this, e);
        });

        container.appendChild(messageCard);

        const initIframe = (iframeId, content) => {
            const iframe = document.getElementById(iframeId);
            if (!iframe) return;

            const isDarkMode = document.body.classList.contains('dark-mode');
            const isHtml = /<[a-z][\s\S]*>/i.test(content);

            const bgColor = isDarkMode ? '#1a1a1a' : '#ffffff';
            const textColor = isDarkMode ? '#e2e8f0' : '#2d3748';

            const doc = iframe.contentWindow.document;
            doc.open();
            doc.write(`
                <!DOCTYPE html>
                <html>
                <head>
                    <base href="${window.location.origin}/" target="_blank">
                    <style>
                        :root { color-scheme: ${isDarkMode ? 'dark' : 'light'}; }
                        body { 
                            font-family: Calibri, 'Segoe UI', Arial, sans-serif; 
                            margin: 8px 12px; 
                            color: ${textColor};
                            background: ${bgColor};
                            word-wrap: break-word;
                            line-height: 1.4;
                            padding-bottom: 8px;
                            cursor: auto;
                            ${isHtml ? '' : 'white-space: pre-wrap; font-size: 14px;'}
                        }
                        img {
                            cursor: pointer;
                            max-width: 100%;
                        }
                        a {
                            cursor: pointer;
                        }
                        * { max-width: 100%; box-sizing: border-box; }
                        table { display: block; overflow-x: auto; border-collapse: collapse; width: 100% !important; height: auto !important; }
                        blockquote { border-left: 4px solid #cbd5e1; padding-left: 1rem; margin: 1rem 0; color: ${isDarkMode ? '#94a3b8' : '#64748b'}; font-style: italic; }
                    </style>
                </head>
                <body spellcheck="false">
                    <div class="email-content-wrapper">${content}</div>
                    <script>
                        function applyUniversalDarkMode() {
                            const isDark = ${isDarkMode};
                            if (!isDark) return;

                            const elements = document.querySelectorAll('.email-content-wrapper, .email-content-wrapper *');
                            elements.forEach(el => {
                                const style = window.getComputedStyle(el);
                                const bg = style.backgroundColor;
                                if (bg && bg !== 'transparent' && bg !== 'rgba(0, 0, 0, 0)') {
                                    const rgb = bg.match(/\\d+/g);
                                    if (rgb && rgb.length >= 3) {
                                        const brightness = (parseInt(rgb[0]) * 299 + parseInt(rgb[1]) * 587 + parseInt(rgb[2]) * 114) / 1000;
                                        if (brightness > 200) {
                                            el.style.setProperty('background-color', 'transparent', 'important');
                                            el.style.setProperty('background-image', 'none', 'important');
                                        }
                                    }
                                }
                                const color = style.color;
                                if (color) {
                                    const rgb = color.match(/\\d+/g);
                                    if (rgb && rgb.length >= 3) {
                                        const brightness = (parseInt(rgb[0]) * 299 + parseInt(rgb[1]) * 587 + parseInt(rgb[2]) * 114) / 1000;
                                        if (brightness < 100) {
                                            el.style.setProperty('color', '${textColor}', 'important');
                                        }
                                    }
                                }
                            });
                        }

                        function sendSize() {
                            const height = document.body.scrollHeight;
                            window.parent.postMessage({
                                type: 'resize-iframe',
                                id: '${iframeId}',
                                height: height + 10
                            }, '*');
                        }

                        document.addEventListener('click', function(e) {
                            if (e.target.tagName === 'IMG') {
                                e.preventDefault();
                                e.stopPropagation();
                                window.open(e.target.src, '_blank');
                                return;
                            }
                            const link = e.target.closest('a');
                            if (link) {
                                link.target = '_blank';
                            }
                        });

                        window.onload = () => {
                            applyUniversalDarkMode();
                            sendSize();
                            setTimeout(sendSize, 500);
                        };

                        window.addEventListener('message', (e) => {
                            if (e.data === 'trigger-resize') sendSize();
                        });
                    <\/script>
                </body>
                </html>
            `);
            doc.close();
        };

        initIframe(`iframe-${msg.message_id || index}`, sanitizedMain);
        if (sanitizedThread) {
            initIframe(`iframe-thread-${msg.message_id || index}`, sanitizedThread);
        }

        if (msg.attachments) {
            try {
                const attList = typeof msg.attachments === 'string' ? JSON.parse(msg.attachments) : msg.attachments;
                attList.forEach((att, idx) => {
                    const uniqueId = `att-${msg.message_id}-${idx}`;
                    const btn = messageCard.querySelector(`#${uniqueId}`);
                    if (btn) {
                        btn.addEventListener('click', (e) => {
                            e.stopPropagation();
                            downloadAttachment(att.id, msg.message_id, att.name);
                        });
                    }
                });
            } catch (e) { }
        }
    });
}

window.addEventListener('message', function (event) {
    if (event.data.type === 'resize-iframe') {
        const iframe = document.getElementById(event.data.id);
        if (iframe) {
            iframe.style.height = (event.data.height) + 'px';
            iframe.setAttribute('scrolling', 'no');
        }
    } else if (event.data.type === 'toggle-message') {
        const iframe = document.getElementById(event.data.id);
        if (iframe) {
            const card = iframe.closest('.message-card');
            if (card) toggleMessage(card, { target: iframe });
        }
    }
});

function handlePreviewClick(btn) {
    const attId = btn.getAttribute('data-att-id');
    const msgId = btn.getAttribute('data-msg-id');
    const filename = btn.getAttribute('data-filename');
    const type = btn.getAttribute('data-type');
    previewAttachment(attId, msgId, filename, type);
}

async function previewAttachment(attachmentId, messageId, filename, contentType) {
    const previewModal = document.getElementById('previewModal');
    const previewTitle = document.getElementById('previewTitle');
    const previewBody = document.getElementById('previewBody');
    const btnDownloadPreview = document.getElementById('btnDownloadPreview');

    previewTitle.textContent = filename;
    previewBody.innerHTML = `
        <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; width: 100%;">
            <div style="width: 40px; height: 40px; border: 4px solid #f3f3f3; border-top: 4px solid #667eea; border-radius: 50%; animation: spin 1s linear infinite;"></div>
            <p style="margin-top: 1rem; color: var(--text-secondary);">Preparing preview...</p>
        </div>
    `;

    if (!document.getElementById('spinStyle')) {
        const style = document.createElement('style');
        style.id = 'spinStyle';
        style.innerHTML = "@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }";
        document.head.appendChild(style);
    }

    previewModal.classList.add('active');

    const viewUrl = `/api/view_attachment?message_id=${encodeURIComponent(messageId)}&attachment_id=${encodeURIComponent(attachmentId)}&filename=${encodeURIComponent(filename)}&content_type=${encodeURIComponent(contentType || '')}`;

    btnDownloadPreview.onclick = (e) => {
        e.stopPropagation();
        downloadAttachment(attachmentId, messageId, filename);
    };

    setTimeout(() => {
        const isImage = contentType && contentType.startsWith('image/');
        const isPDF = contentType === 'application/pdf' || filename.toLowerCase().endsWith('.pdf');

        if (isImage) {
            previewBody.innerHTML = `<img src="${viewUrl}" alt="${escapeHtml(filename)}" onerror="this.parentElement.innerHTML='<div style=\'padding:2rem;color:red;\'>❌ Failed to load image preview.</div>'">`;
        } else if (isPDF) {
            previewBody.innerHTML = `<iframe src="${viewUrl}" style="border:none; width:100%; height:100%;" title="PDF Preview"></iframe>`;
        } else {
            previewBody.innerHTML = '<div style="padding: 2rem; color: var(--text-primary);">Preview not available for this file type.</div>';
        }
    }, 100);
}

function closePreview() {
    const modal = document.getElementById('previewModal');
    if (modal) modal.classList.remove('active');
    const body = document.getElementById('previewBody');
    if (body) body.innerHTML = '';
}

async function downloadAttachment(attachmentId, messageId, filename) {
    const url = `/api/download_attachment?attachment_id=${encodeURIComponent(attachmentId)}&message_id=${encodeURIComponent(messageId)}&attachment_name=${encodeURIComponent(filename)}`;

    try {
        const response = await fetch(url);
        if (!response.ok) throw new Error('Download failed');

        const blob = await response.blob();
        const objectUrl = window.URL.createObjectURL(blob);

        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = objectUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();

        window.URL.revokeObjectURL(objectUrl);
        document.body.removeChild(a);
    } catch (error) {
        console.error('Error downloading attachment:', error);
        alert('Failed to download attachment. Please try again.');
    }
}

function closeModal() {
    try {
        console.log('Closing ticket modal...');
        if (currentTicketIdStr && socket && socket.connected) {
            socket.emit('unlock_ticket', { ticket_id: currentTicketIdStr });
        }

        const modal = document.getElementById('ticketModal');
        if (modal) modal.classList.remove('active');
        document.body.classList.remove('modal-open');
        currentTicketRow = null;
        currentTicketIdStr = null;
        // if (quillEditor) quillEditor.setText('');
    } catch (error) {
        console.error('Error in closeModal:', error);
        const modal = document.getElementById('ticketModal');
        if (modal) modal.classList.remove('active');
    }
}

function showToast(message, isError = false) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.classList.remove('error');
    if (isError) {
        toast.classList.add('error');
    }
    toast.classList.add('show');

    setTimeout(() => {
        toast.classList.remove('show');
    }, 3000);
}

async function changeAssignment() {
    if (!currentTicketRow || !currentTicketIdStr) return;

    const newAssignee = document.getElementById('modal-assignment').value;

    try {
        const response = await fetch('/api/take_ticket', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ticket_id: currentTicketIdStr,
                row_number: currentTicketRow,
                assigned_to: newAssignee
            })
        });

        const result = await response.json();
        if (result.success) {
            showToast(`Ticket assigned to ${newAssignee}`, false);
            loadTickets();
        } else {
            showToast(result.error || 'Failed to change assignment', true);
        }
    } catch (error) {
        console.error('Error changing assignment:', error);
        showToast('Network error while assigning', true);
    }
}

async function takeTicket() {
    if (!currentTicketIdStr) return;

    try {
        const response = await fetch('/api/take_ticket', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ticket_id: currentTicketIdStr,
                row_number: currentTicketRow
            })
        });

        const result = await response.json();
        if (result.success) {
            showToast('Ticket assigned to you!', false);
            const assignmentSelect = document.getElementById('modal-assignment');
            if (assignmentSelect) {
                assignmentSelect.value = result.assigned_to;
            }
            loadTickets();
        } else {
            showToast(result.error || 'Failed to take ticket', true);
        }
    } catch (error) {
        console.error('Error taking ticket:', error);
        showToast('Network error while taking ticket', true);
    }
}

// Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Tiptap v2 Editor and Azure DevOps Integration Logic - start
async function uploadInlineImageFile(file) {
    const formData = new FormData();
    formData.append('file', file);
    try {
        const resp = await fetch('/api/upload_inline_image', {
            method: 'POST',
            body: formData
        });
        if (resp.ok) {
            return await resp.json();
        }
    } catch (err) {
        console.error('Failed to upload inline image:', err);
    }
    return null;
}

// Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Tiptap Instances for Email, DevOps Description and Repro Steps - start
let devopsDescEditor = null;
let devopsReproEditor = null;

function setupTiptapEditorHelper(elementId, toolbarId, imageInputId, placeholder = '') {
    const editorElem = document.getElementById(elementId);
    if (!editorElem || !window.TiptapModules) return null;

    const { Editor, StarterKit, Image, Link, Underline } = window.TiptapModules;
    const extensions = [
        StarterKit.configure({
            history: { depth: 50 }
        }),
        Image.configure({
            inline: true,
            allowBase64: true
        }),
        Link.configure({
            openOnClick: false
        }),
        Underline
    ];

    const editorInstance = new Editor({
        element: editorElem,
        extensions: extensions,
        content: '',
        editorProps: {
            attributes: {
                class: 'tiptap-prosemirror-content',
                spellcheck: 'true'
            },
            handlePaste: (view, event) => {
                const items = (event.clipboardData || event.originalEvent.clipboardData).items;
                for (const item of items) {
                    if (item.type && item.type.indexOf('image') === 0) {
                        const blob = item.getAsFile();
                        if (blob) {
                            uploadInlineImageFile(blob).then(res => {
                                if (res && res.url && editorInstance) {
                                    editorInstance.chain().focus().setImage({ src: res.url, alt: res.name || 'image', title: res.cid }).run();
                                }
                            });
                            return true;
                        }
                    }
                }
                return false;
            },
            handleDrop: (view, event, slice, moved) => {
                if (!moved && event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0]) {
                    const file = event.dataTransfer.files[0];
                    if (file.type && file.type.startsWith('image/')) {
                        uploadInlineImageFile(file).then(res => {
                            if (res && res.url && editorInstance) {
                                editorInstance.chain().focus().setImage({ src: res.url, alt: res.name || 'image', title: res.cid }).run();
                            }
                        });
                        return true;
                    }
                }
                return false;
            }
        },
        onTransaction: () => {
            updateGenericTiptapToolbar(editorInstance, toolbarId);
        }
    });

    editorElem.onclick = (e) => {
        if (editorInstance && !editorInstance.isFocused && e.target === editorElem) {
            editorInstance.commands.focus('end');
        }
    };

    const toolbar = document.getElementById(toolbarId);
    if (toolbar) {
        toolbar.querySelectorAll('.toolbar-btn').forEach(btn => {
            btn.onclick = (e) => {
                e.preventDefault();
                const action = btn.getAttribute('data-action');
                if (!editorInstance) return;

                if (action === 'bold') editorInstance.chain().focus().toggleBold().run();
                else if (action === 'italic') editorInstance.chain().focus().toggleItalic().run();
                else if (action === 'underline') editorInstance.chain().focus().toggleUnderline().run();
                else if (action === 'strike') editorInstance.chain().focus().toggleStrike().run();
                else if (action === 'bulletList') editorInstance.chain().focus().toggleBulletList().run();
                else if (action === 'orderedList') editorInstance.chain().focus().toggleOrderedList().run();
                else if (action === 'blockquote') editorInstance.chain().focus().toggleBlockquote().run();
                else if (action === 'undo') editorInstance.chain().focus().undo().run();
                else if (action === 'redo') editorInstance.chain().focus().redo().run();
                else if (action === 'link') {
                    const prevUrl = editorInstance.getAttributes('link').href || '';
                    const url = prompt('Enter link URL:', prevUrl);
                    if (url === null) return;
                    if (url === '') {
                        editorInstance.chain().focus().extendMarkRange('link').unsetLink().run();
                    } else {
                        editorInstance.chain().focus().extendMarkRange('link').setLink({ href: url }).run();
                    }
                } else if (action === 'image') {
                    const imgInput = document.getElementById(imageInputId);
                    if (imgInput) imgInput.click();
                }
            };
        });
    }

    const imgInput = document.getElementById(imageInputId);
    if (imgInput) {
        imgInput.onchange = async (e) => {
            const file = e.target.files[0];
            if (file) {
                const res = await uploadInlineImageFile(file);
                if (res && res.url && editorInstance) {
                    editorInstance.chain().focus().setImage({ src: res.url, alt: res.name || 'image', title: res.cid }).run();
                }
            }
            imgInput.value = '';
        };
    }

    return editorInstance;
}

function updateGenericTiptapToolbar(editorInstance, toolbarId) {
    if (!editorInstance) return;
    const toolbar = document.getElementById(toolbarId);
    if (!toolbar) return;

    toolbar.querySelectorAll('.toolbar-btn').forEach(btn => {
        const action = btn.getAttribute('data-action');
        if (action === 'bold') btn.classList.toggle('is-active', editorInstance.isActive('bold'));
        else if (action === 'italic') btn.classList.toggle('is-active', editorInstance.isActive('italic'));
        else if (action === 'underline') btn.classList.toggle('is-active', editorInstance.isActive('underline'));
        else if (action === 'strike') btn.classList.toggle('is-active', editorInstance.isActive('strike'));
        else if (action === 'bulletList') btn.classList.toggle('is-active', editorInstance.isActive('bulletList'));
        else if (action === 'orderedList') btn.classList.toggle('is-active', editorInstance.isActive('orderedList'));
        else if (action === 'blockquote') btn.classList.toggle('is-active', editorInstance.isActive('blockquote'));
        else if (action === 'link') btn.classList.toggle('is-active', editorInstance.isActive('link'));
    });
}

function initTiptapEditor() {
    // 1. Reply Composer Editor
    if (tiptapEditor) {
        tiptapEditor.destroy();
        tiptapEditor = null;
    }
    tiptapEditor = setupTiptapEditorHelper('tiptapEditor', 'tiptapToolbar', 'tiptapImageInput');

    // 2. DevOps Description Editor
    if (devopsDescEditor) {
        devopsDescEditor.destroy();
        devopsDescEditor = null;
    }
    devopsDescEditor = setupTiptapEditorHelper('devopsDescEditor', 'devopsDescToolbar', 'devopsDescImageInput');

    // 3. DevOps Repro Steps Editor
    if (devopsReproEditor) {
        devopsReproEditor.destroy();
        devopsReproEditor = null;
    }
    devopsReproEditor = setupTiptapEditorHelper('devopsReproEditor', 'devopsReproToolbar', 'devopsReproImageInput');
}

// Listen for ESM ready event in case module loads asynchronously
window.addEventListener('tiptap-ready', () => {
    initTiptapEditor();
});

function updateTiptapToolbar() {
    updateGenericTiptapToolbar(tiptapEditor, 'tiptapToolbar');
}
// Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Tiptap Instances for Email, DevOps Description and Repro Steps - end

async function saveTicket() {
    if (isTicketLocked) return;

    const emailContent = tiptapEditor ? tiptapEditor.getHTML() : '';
    const assignedTo = document.getElementById('modal-assignment').value;

    try {
        const response = await fetch('/api/update_ticket', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                row_number: currentTicketRow,
                ticket_id: currentTicketIdStr,
                ai_response: emailContent,
                assigned_to: assignedTo
            })
        });

        const data = await response.json();

        if (response.ok && data.success) {
            showToast('✓ Draft saved successfully');
            loadTickets();
        } else {
            showToast(data.error || 'Failed to save ticket', true);
        }
    } catch (error) {
        console.error('Error saving draft:', error);
        showToast('Network error while saving draft', true);
    }
}

function showSendingOverlay() {
    let overlay = document.getElementById('sending-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'sending-overlay';
        overlay.innerHTML = `
            <div class="sending-overlay-content" style="background: var(--bg-card); padding: 2rem; border-radius: 12px; border: 1px solid var(--border-color); text-align: center; box-shadow: 0 10px 30px rgba(0,0,0,0.3); max-width: 380px;">
                <div class="sending-spinner" style="width: 40px; height: 40px; border: 4px solid var(--border-color); border-top: 4px solid #3A5A24; border-radius: 50%; animation: spin 1s linear infinite; margin: 0 auto 1rem;"></div>
                <h3 style="font-size: 1.1rem; color: var(--text-primary); margin-bottom: 0.5rem;">Transmitting Message</h3>
                <p style="font-size: 0.85rem; color: var(--text-secondary); margin: 0;">Uploading inline assets and sending reply via Microsoft Graph...</p>
            </div>
        `;
        overlay.style.position = 'fixed';
        overlay.style.top = '0';
        overlay.style.left = '0';
        overlay.style.width = '100%';
        overlay.style.height = '100%';
        overlay.style.background = 'rgba(0,0,0,0.5)';
        overlay.style.display = 'none';
        overlay.style.alignItems = 'center';
        overlay.style.justifyContent = 'center';
        overlay.style.zIndex = '3000';
        document.body.appendChild(overlay);
    }
    overlay.style.display = 'flex';
}

function hideSendingOverlay() {
    const overlay = document.getElementById('sending-overlay');
    if (overlay) overlay.style.display = 'none';
}

// Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Modern Outbound Email Attachments Logic with Deduplication and Lightbox - start
let emailPendingAttachments = [];

function handleEmailFileSelect(e) {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    for (let i = 0; i < files.length; i++) {
        addEmailAttachment(files[i]);
    }
    e.target.value = '';
}

function addEmailAttachment(file) {
    if (!file) return;
    // Deduplication check
    if (emailPendingAttachments.some(a => a.name === file.name)) {
        showToast(`"${file.name}" is already attached.`, true);
        return;
    }
    const isImage = file.type.startsWith('image/') || /\.(png|jpe?g|gif|webp|bmp)$/i.test(file.name);
    const previewUrl = URL.createObjectURL(file);
    emailPendingAttachments.push({
        file: file,
        name: file.name || 'attachment',
        size: file.size || 0,
        type: file.type || (isImage ? 'image/png' : 'application/octet-stream'),
        previewUrl: previewUrl
    });
    renderEmailAttachmentPreviews();
}

function removeEmailAttachment(index) {
    if (index >= 0 && index < emailPendingAttachments.length) {
        const item = emailPendingAttachments[index];
        if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
        emailPendingAttachments.splice(index, 1);
        renderEmailAttachmentPreviews();
    }
}

function previewPendingAttachment(index, source = 'email') {
    const list = source === 'email' ? emailPendingAttachments : devopsPendingAttachments;
    if (index < 0 || index >= list.length) return;
    const att = list[index];
    if (!att) return;

    const previewModal = document.getElementById('previewModal');
    const previewTitle = document.getElementById('previewTitle');
    const previewBody = document.getElementById('previewBody');
    const btnDownloadPreview = document.getElementById('btnDownloadPreview');
    if (!previewModal || !previewBody) return;

    previewTitle.textContent = att.name;
    previewModal.classList.add('active');

    btnDownloadPreview.onclick = (e) => {
        e.stopPropagation();
        if (att.file) {
            const a = document.createElement('a');
            a.href = att.previewUrl || URL.createObjectURL(att.file);
            a.download = att.name;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        }
    };

    const isImage = att.type.startsWith('image/') || /\.(png|jpe?g|gif|webp|bmp)$/i.test(att.name);
    const isPDF = att.type === 'application/pdf' || att.name.toLowerCase().endsWith('.pdf');

    if (isImage && att.previewUrl) {
        previewBody.innerHTML = `<img src="${att.previewUrl}" alt="${escapeHtml(att.name)}" style="max-width: 100%; max-height: 80vh; object-fit: contain;">`;
    } else if (isPDF && att.previewUrl) {
        previewBody.innerHTML = `<iframe src="${att.previewUrl}" style="border:none; width:100%; height:75vh;" title="PDF Preview"></iframe>`;
    } else {
        previewBody.innerHTML = `
            <div style="text-align: center; padding: 2rem; color: var(--text-primary);">
                <div style="font-size: 3rem; margin-bottom: 0.5rem;">📄</div>
                <h5>${escapeHtml(att.name)}</h5>
                <p style="color: var(--text-secondary); font-size: 0.9rem;">${formatFileSize(att.size)}</p>
                <p style="color: var(--text-secondary); font-size: 0.85rem;">Preview not available for this file type. Click download to view.</p>
            </div>
        `;
    }
}

function renderEmailAttachmentPreviews() {
    const container = document.getElementById('emailAttachmentPreviewList');
    if (!container) return;

    if (emailPendingAttachments.length === 0) {
        container.innerHTML = '';
        return;
    }

    container.innerHTML = emailPendingAttachments.map((att, idx) => {
        const isImage = att.type.startsWith('image/') || /\.(png|jpe?g|gif|webp)$/i.test(att.name);
        const isVideo = att.type.startsWith('video/') || /\.(mp4|webm|mov|mkv)$/i.test(att.name);
        const isPDF = att.type === 'application/pdf' || att.name.toLowerCase().endsWith('.pdf');
        const isExcel = /\.(xlsx|xls|csv)$/i.test(att.name);
        const isDoc = /\.(docx|doc|txt)$/i.test(att.name);

        let icon = '📁';
        if (isVideo) icon = '🎬';
        else if (isPDF) icon = '📕';
        else if (isExcel) icon = '📊';
        else if (isDoc) icon = '📄';
        else if (isImage) icon = '🖼️';

        const previewEl = (isImage && att.previewUrl)
            ? `<img src="${att.previewUrl}" class="devops-tile-img" alt="${escapeHtml(att.name)}">`
            : `<div class="devops-tile-icon">${icon}</div>`;

        return `
            <div class="devops-tile-card">
                <div class="devops-tile-preview" onclick="previewPendingAttachment(${idx}, 'email')" style="cursor: pointer;" title="Click to view ${escapeHtml(att.name)}">
                    ${previewEl}
                </div>
                <div class="devops-tile-footer">
                    <div class="devops-tile-info">
                        <div class="devops-tile-name" title="${escapeHtml(att.name)}">${escapeHtml(att.name)}</div>
                        <div class="devops-tile-size">${formatFileSize(att.size)}</div>
                    </div>
                    <button type="button" class="devops-tile-remove" onclick="removeEmailAttachment(${idx})" title="Remove attachment">&times;</button>
                </div>
            </div>
        `;
    }).join('');
}

function populateEmailTicketChips(messages) {
    const chipsWrapper = document.getElementById('emailTicketAttachmentChips');
    const container = document.getElementById('emailTicketChipsContainer');
    if (!chipsWrapper || !container) return;

    const allAttachments = [];

    (messages || []).forEach(msg => {
        if (msg.attachments) {
            let atts = msg.attachments;
            if (typeof atts === 'string') {
                try { atts = JSON.parse(atts); } catch(e) { atts = []; }
            }
            if (Array.isArray(atts)) {
                atts.forEach(a => {
                    const name = a.name || a.filename || 'attachment';
                    const cType = a.content_type || a.contentType || '';
                    if (!allAttachments.some(existing => existing.name === name)) {
                        allAttachments.push({ ...a, msgId: msg.message_id || msg.id, name: name, contentType: cType });
                    }
                });
            }
        }
    });

    if (allAttachments.length === 0) {
        chipsWrapper.classList.add('d-none');
        container.innerHTML = '';
        return;
    }

    chipsWrapper.classList.remove('d-none');
    container.innerHTML = allAttachments.map(att => `
        <button type="button" class="devops-ticket-chip" onclick="importTicketAttachmentToEmail('${escapeHtml(att.name)}', '${encodeURIComponent(att.msgId || '')}', '${encodeURIComponent(att.id || '')}', '${encodeURIComponent(att.contentType || '')}')" title="Attach ${escapeHtml(att.name)} to reply">
            <span>📎 ${escapeHtml(att.name)}</span>
            <i class="fa fa-plus ms-1" style="font-size: 0.65rem;"></i>
        </button>
    `).join('');
}

async function importTicketAttachmentToEmail(filename, msgId, attId, contentType) {
    if (!msgId || !attId) return;
    if (emailPendingAttachments.some(a => a.name === filename)) {
        showToast(`"${filename}" is already attached.`, true);
        return;
    }
    try {
        const downloadUrl = `/api/view_attachment?message_id=${msgId}&attachment_id=${attId}&filename=${encodeURIComponent(filename)}&content_type=${encodeURIComponent(contentType || '')}`;
        const resp = await fetch(downloadUrl);
        if (resp.ok) {
            const blob = await resp.blob();
            const isImg = (contentType && contentType.startsWith('image/')) || /\.(jpg|jpeg|png|gif|webp|bmp)$/i.test(filename);
            const mimeType = (blob.type && blob.type !== 'application/octet-stream') ? blob.type : (contentType || (isImg ? 'image/png' : 'application/octet-stream'));
            const file = new File([blob], filename, { type: mimeType });
            addEmailAttachment(file);
            showToast(`Attached ${filename}`, false);
        } else {
            showToast(`Could not download ${filename}`, true);
        }
    } catch(err) {
        console.error('Error importing ticket attachment:', err);
    }
}
// Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Modern Outbound Email Attachments Logic with Deduplication and Lightbox - end
// Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Modern Outbound Email Attachments Logic - end

async function sendToCustomer() {
    if (isTicketLocked) return;

    const emailContent = tiptapEditor ? tiptapEditor.getHTML() : '';
    const assignedTo = document.getElementById('modal-assignment').value;
    const sendTo = document.getElementById('emailTo').value;

    const isEditorEmpty = !emailContent || emailContent.trim() === '' || emailContent === '<p></p>' || emailContent === '<p><br></p>';
    if (isEditorEmpty) {
        alert('Please write a reply response before sending.');
        return;
    }

    if (!sendTo) {
        alert('Recipient email is missing.');
        return;
    }

    const formData = new FormData();
    formData.append('ticket_id', currentTicketIdStr);
    formData.append('ai_response', emailContent);
    formData.append('assigned_to', assignedTo);
    formData.append('send_to', sendTo);
    formData.append('cc', document.getElementById('emailCC') ? document.getElementById('emailCC').value : '');
    formData.append('bcc', document.getElementById('emailBCC') ? document.getElementById('emailBCC').value : '');

    emailPendingAttachments.forEach((att, index) => {
        formData.append(`attachment_${index}`, att.file);
    });
    formData.append('attachment_count', emailPendingAttachments.length);

    showSendingOverlay();

    try {
        const response = await fetch('/api/send_to_customer', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        if (!response.ok) {
            alert(data.error || 'Failed to send email.');
            return;
        }

        showToast('Email sent successfully! Ticket remains open.', false);
        emailPendingAttachments = [];
        renderEmailAttachmentPreviews();
        loadTickets();
        
        // Refresh conversation thread in modal
        if (currentTicketIdStr) {
            const res = await fetch(`/api/ticket_messages/${currentTicketIdStr}`);
            if (res.ok) {
                const d = await res.json();
                renderThread(d.messages || []);
                populateEmailTicketChips(d.messages || []);
            }
        }
    } catch (error) {
        console.error('Error sending email:', error);
        alert('Error sending email. Please try again.');
    } finally {
        hideSendingOverlay();
    }
}

// Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Azure DevOps Floating Dropdown Menu Logic - start
let currentLinkedDevOpsItems = [];

function toggleDevOpsDropdown(e, forceState = null) {
    if (e) {
        e.preventDefault();
        e.stopPropagation();
    }
    const menu = document.getElementById('devopsDropdownMenu');
    if (!menu) return;
    if (forceState !== null) {
        menu.classList.toggle('d-none', !forceState);
    } else {
        menu.classList.toggle('d-none');
    }
}

// Close dropdown when clicking outside
document.addEventListener('click', (e) => {
    const wrapper = document.querySelector('.devops-dropdown-wrapper');
    if (wrapper && !wrapper.contains(e.target)) {
        const menu = document.getElementById('devopsDropdownMenu');
        if (menu && !menu.classList.contains('d-none')) {
            menu.classList.add('d-none');
        }
    }
});

function toggleDevOpsSection(forceOpen = false) {
    toggleDevOpsDropdown(null, forceOpen ? true : null);
}

function toggleDevOpsLinkInput() {
    const row = document.getElementById('devopsLinkRow');
    if (row) {
        row.classList.toggle('d-none');
        if (!row.classList.contains('d-none')) {
            const input = document.getElementById('devopsLinkIdInput');
            if (input) input.focus();
        }
    }
}

async function loadLinkedDevOpsItems(ticketId) {
    if (!ticketId) return;
    const listContainer = document.getElementById('devopsItemsList');
    const badge = document.getElementById('devopsCountBadge');
    const headerBadge = document.getElementById('headerDevOpsBadge');
    const refreshIcon = document.getElementById('devopsRefreshIcon');
    const unlinkHeaderBtn = document.getElementById('devopsUnlinkHeaderBtn');
    if (refreshIcon) refreshIcon.classList.add('fa-spin');

    try {
        const resp = await fetch(`/api/devops/linked/${encodeURIComponent(ticketId)}`);
        if (resp.ok) {
            const data = await resp.json();
            const items = data.work_items || [];
            currentLinkedDevOpsItems = items;
            const count = items.length;
            if (badge) badge.textContent = count;
            if (headerBadge) headerBadge.textContent = count;

            // Show 3 buttons (Create New, Link, Unlink) when count > 0; 2 buttons when count == 0
            if (unlinkHeaderBtn) {
                if (count > 0) {
                    unlinkHeaderBtn.classList.remove('d-none');
                } else {
                    unlinkHeaderBtn.classList.add('d-none');
                }
            }

            renderDevOpsCards(items, data.configured);
        } else {
            if (listContainer) listContainer.innerHTML = '<div class="devops-empty-state text-danger">Failed to fetch DevOps items</div>';
        }
    } catch (err) {
        console.error('Error fetching DevOps items:', err);
        if (listContainer) listContainer.innerHTML = '<div class="devops-empty-state text-danger">Network error connecting to Azure DevOps</div>';
    } finally {
        if (refreshIcon) refreshIcon.classList.remove('fa-spin');
    }
}

function renderDevOpsCards(items, configured) {
    const listContainer = document.getElementById('devopsItemsList');
    if (!listContainer) return;

    if (!items || items.length === 0) {
        listContainer.innerHTML = `
            <div class="devops-zero-page">
                <div class="devops-zero-icon">📋</div>
                <div class="devops-zero-title">No Azure DevOps Work Items Linked</div>
                <div class="devops-zero-desc">
                    ${configured ? 'Link an existing Azure DevOps work item or create a new one directly for this ticket.' : 'Azure DevOps is not configured in .env.'}
                </div>
                <div class="devops-zero-actions">
                    <button type="button" class="btn btn-sm btn-primary" onclick="openDevOpsCreateDrawer(this)">
                        <i class="fa fa-plus"></i> Create New
                    </button>
                    <button type="button" class="btn btn-sm btn-outline-secondary" onclick="toggleDevOpsLinkInput()">
                        <i class="fa fa-link"></i> Link Ticket
                    </button>
                </div>
            </div>
        `;
        return;
    }

    let html = '';
    items.forEach(item => {
        const typeIcon = item.type === 'Bug' ? '🪲' : (item.type === 'User Story' ? '📖' : '📋');
        const stateClass = `devops-state-${(item.state || 'new').toLowerCase().replace(/\s+/g, '')}`;
        const tagsHtml = (item.tags || []).map(t => `<span class="devops-tag-pill">🏷️ ${escapeHtml(t)}</span>`).join('');

        html += `
            <div class="devops-card">
                <div class="devops-card-title-row">
                    <div class="devops-card-title">${escapeHtml(item.title)}</div>
                    <button type="button" class="devops-card-unlink-btn" onclick="unlinkDevOpsItem(${item.id})" title="Unlink Work Item #${item.id}">✕</button>
                </div>
                <div class="devops-card-meta">
                    <span class="devops-badge devops-badge-id">#${item.id}</span>
                    <span class="devops-badge devops-badge-type">${typeIcon} ${escapeHtml(item.type)}</span>
                    <span>👤 ${escapeHtml(item.assigned_to)}</span>
                    <span class="devops-state-pill ${stateClass}">${escapeHtml(item.state)}</span>
                </div>
                ${tagsHtml ? `<div class="devops-card-tags">${tagsHtml}</div>` : ''}
                <div class="devops-card-actions">
                    <a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer" class="devops-open-link" onclick="event.stopPropagation()">
                        <span>Open in Azure DevOps</span> ↗
                    </a>
                </div>
            </div>
        `;
    });

    listContainer.innerHTML = html;
}

async function promptUnlinkDevOpsItem() {
    if (!currentLinkedDevOpsItems || currentLinkedDevOpsItems.length === 0) {
        showToast('No linked items to unlink', true);
        return;
    }

    if (currentLinkedDevOpsItems.length === 1) {
        const item = currentLinkedDevOpsItems[0];
        unlinkDevOpsItem(item.id);
        return;
    }

    const idStr = prompt(`Enter the Work Item #ID to unlink (Available: ${currentLinkedDevOpsItems.map(i => '#' + i.id).join(', ')}):`);
    if (idStr && idStr.trim()) {
        const cleanId = idStr.replace('#', '').trim();
        unlinkDevOpsItem(cleanId);
    }
}
// Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Azure DevOps Zero Page & Dynamic 2/3 Action Buttons - end

async function submitDevOpsLink() {
    const input = document.getElementById('devopsLinkIdInput');
    if (!input || !input.value.trim() || !currentTicketIdStr) return;

    const rawId = input.value.trim();
    try {
        const resp = await fetch('/api/devops/link', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ticket_id: currentTicketIdStr,
                work_item_id: rawId
            })
        });

        const data = await resp.json();
        if (resp.ok && data.success) {
            showToast(`Linked Work Item #${rawId}`, false);
            input.value = '';
            toggleDevOpsLinkInput();
            loadLinkedDevOpsItems(currentTicketIdStr);
        } else {
            alert(data.error || 'Failed to link work item');
        }
    } catch (err) {
        console.error('Error linking DevOps item:', err);
        alert('Network error while linking work item');
    }
}

async function unlinkDevOpsItem(workItemId) {
    if (!confirm(`Are you sure you want to unlink Work Item #${workItemId} from this ticket?`)) return;
    if (!currentTicketIdStr) return;

    try {
        const resp = await fetch('/api/devops/unlink', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ticket_id: currentTicketIdStr,
                work_item_id: workItemId
            })
        });

        if (resp.ok) {
            showToast(`Unlinked Work Item #${workItemId}`, false);
            loadLinkedDevOpsItems(currentTicketIdStr);
        } else {
            const data = await resp.json();
            alert(data.error || 'Failed to unlink work item');
        }
    } catch (err) {
        console.error('Error unlinking DevOps item:', err);
        alert('Network error while unlinking work item');
    }
}

// Bilal Khan (31/08/2026) Issue No  Sheet_Name  - DevOps Media Attachments & Inline Image Resizer Logic - start
let devopsPendingAttachments = [];
let activeTiptapImage = null;

function handleDevOpsFileSelect(e) {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    for (let i = 0; i < files.length; i++) {
        addDevOpsAttachment(files[i]);
    }
    e.target.value = '';
}

function addDevOpsAttachment(file, uploadedUrl = null) {
    if (!file) return;
    // Deduplication check
    if (devopsPendingAttachments.some(a => a.name === file.name)) {
        showToast(`"${file.name}" is already attached.`, true);
        return;
    }
    const isImage = file.type.startsWith('image/') || /\.(png|jpe?g|gif|webp|bmp)$/i.test(file.name);
    const previewUrl = URL.createObjectURL(file);
    devopsPendingAttachments.push({
        file: file,
        name: file.name || 'attachment.png',
        size: file.size || 0,
        type: file.type || (isImage ? 'image/png' : 'application/octet-stream'),
        previewUrl: previewUrl,
        uploadedUrl: uploadedUrl
    });
    renderDevOpsAttachmentPreviews();
}

function removeDevOpsAttachment(index) {
    if (index >= 0 && index < devopsPendingAttachments.length) {
        const item = devopsPendingAttachments[index];
        if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
        devopsPendingAttachments.splice(index, 1);
        renderDevOpsAttachmentPreviews();
    }
}

function renderDevOpsAttachmentPreviews() {
    const container = document.getElementById('devopsAttachmentPreviewList');
    if (!container) return;

    if (devopsPendingAttachments.length === 0) {
        container.innerHTML = '';
        return;
    }

    container.innerHTML = devopsPendingAttachments.map((att, idx) => {
        const isImage = att.type.startsWith('image/') || /\.(png|jpe?g|gif|webp)$/i.test(att.name);
        const isVideo = att.type.startsWith('video/') || /\.(mp4|webm|mov|mkv)$/i.test(att.name);
        const isPDF = att.type === 'application/pdf' || att.name.toLowerCase().endsWith('.pdf');
        const isExcel = /\.(xlsx|xls|csv)$/i.test(att.name);
        const isDoc = /\.(docx|doc|txt)$/i.test(att.name);

        let icon = '📁';
        if (isVideo) icon = '🎬';
        else if (isPDF) icon = '📕';
        else if (isExcel) icon = '📊';
        else if (isDoc) icon = '📄';
        else if (isImage) icon = '🖼️';

        const previewEl = (isImage && att.previewUrl)
            ? `<img src="${att.previewUrl}" class="devops-tile-img" alt="${escapeHtml(att.name)}">`
            : `<div class="devops-tile-icon">${icon}</div>`;

        return `
            <div class="devops-tile-card">
                <div class="devops-tile-preview" onclick="previewPendingAttachment(${idx}, 'devops')" style="cursor: pointer;" title="Click to view ${escapeHtml(att.name)}">
                    ${previewEl}
                </div>
                <div class="devops-tile-footer">
                    <div class="devops-tile-info">
                        <div class="devops-tile-name" title="${escapeHtml(att.name)}">${escapeHtml(att.name)}</div>
                        <div class="devops-tile-size">${formatFileSize(att.size)}</div>
                    </div>
                    <button type="button" class="devops-tile-remove" onclick="removeDevOpsAttachment(${idx})" title="Remove attachment">&times;</button>
                </div>
            </div>
        `;
    }).join('');
}

function populateDevOpsTicketChips() {
    const chipsWrapper = document.getElementById('devopsTicketAttachmentChips');
    const container = document.getElementById('devopsTicketChipsContainer');
    if (!chipsWrapper || !container) return;

    const activeTicket = currentTickets.find(t => t.id === currentTicketRow || t.ticket_id === currentTicketIdStr);
    const messages = (activeTicket && activeTicket.messages) ? activeTicket.messages : [];
    const allAttachments = [];

    messages.forEach(msg => {
        if (msg.attachments) {
            let atts = msg.attachments;
            if (typeof atts === 'string') {
                try { atts = JSON.parse(atts); } catch(e) { atts = []; }
            }
            if (Array.isArray(atts)) {
                atts.forEach(a => {
                    const name = a.name || a.filename || 'attachment';
                    const cType = a.content_type || a.contentType || '';
                    if (!allAttachments.some(existing => existing.name === name)) {
                        allAttachments.push({ ...a, msgId: msg.message_id || msg.id, name: name, contentType: cType });
                    }
                });
            }
        }
    });

    if (allAttachments.length === 0) {
        chipsWrapper.classList.add('d-none');
        container.innerHTML = '';
        return;
    }

    chipsWrapper.classList.remove('d-none');
    container.innerHTML = allAttachments.map(att => `
        <button type="button" class="devops-ticket-chip" onclick="importTicketAttachmentToDevOps('${escapeHtml(att.name)}', '${encodeURIComponent(att.msgId || '')}', '${encodeURIComponent(att.id || '')}', '${encodeURIComponent(att.contentType || '')}')" title="Attach ${escapeHtml(att.name)} to Azure DevOps">
            <span>📎 ${escapeHtml(att.name)}</span>
            <i class="fa fa-plus ms-1" style="font-size: 0.65rem;"></i>
        </button>
    `).join('');
}

async function importTicketAttachmentToDevOps(filename, msgId, attId, contentType) {
    if (!msgId || !attId) return;
    if (devopsPendingAttachments.some(a => a.name === filename)) {
        showToast(`"${filename}" is already attached.`, true);
        return;
    }
    try {
        const downloadUrl = `/api/view_attachment?message_id=${msgId}&attachment_id=${attId}&filename=${encodeURIComponent(filename)}&content_type=${encodeURIComponent(contentType || '')}`;
        const resp = await fetch(downloadUrl);
        if (resp.ok) {
            const blob = await resp.blob();
            const isImg = (contentType && contentType.startsWith('image/')) || /\.(jpg|jpeg|png|gif|webp|bmp)$/i.test(filename);
            const mimeType = (blob.type && blob.type !== 'application/octet-stream') ? blob.type : (contentType || (isImg ? 'image/png' : 'application/octet-stream'));
            const file = new File([blob], filename, { type: mimeType });
            addDevOpsAttachment(file);
            showToast(`Attached ${filename}`, false);
        } else {
            showToast(`Could not download ${filename}`, true);
        }
    } catch(err) {
        console.error('Error importing ticket attachment:', err);
    }
}

// Global Paste & Drag/Drop listener for DevOps Drawer
document.addEventListener('DOMContentLoaded', () => {
    if (window.TiptapModules && !tiptapEditor) {
        initTiptapEditor();
    }

    // 1. Drag & Drop on DevOps Drawer Dropzone
    const dropzone = document.getElementById('devopsAttachmentDropzone');
    if (dropzone) {
        ['dragenter', 'dragover'].forEach(eventName => {
            dropzone.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropzone.classList.add('dragover');
            });
        });

        ['dragleave', 'drop'].forEach(eventName => {
            dropzone.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropzone.classList.remove('dragover');
            });
        });

        dropzone.addEventListener('drop', (e) => {
            const dt = e.dataTransfer;
            const files = dt ? dt.files : null;
            if (files && files.length > 0) {
                for (let i = 0; i < files.length; i++) {
                    addDevOpsAttachment(files[i]);
                }
            }
        });
    }

    // 1b. Drag & Drop on Email Composer Dropzone
    const emailDropzone = document.getElementById('emailAttachmentDropzone');
    if (emailDropzone) {
        ['dragenter', 'dragover'].forEach(eventName => {
            emailDropzone.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                emailDropzone.classList.add('dragover');
            });
        });

        ['dragleave', 'drop'].forEach(eventName => {
            emailDropzone.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                emailDropzone.classList.remove('dragover');
            });
        });

        emailDropzone.addEventListener('drop', (e) => {
            const dt = e.dataTransfer;
            const files = dt ? dt.files : null;
            if (files && files.length > 0) {
                for (let i = 0; i < files.length; i++) {
                    addEmailAttachment(files[i]);
                }
            }
        });
    }

    // 2. Global Clipboard Paste Listener for Attachments (Ctrl+V) - Excludes Rich Text Editors to prevent duplication
    window.addEventListener('paste', (e) => {
        const drawer = document.getElementById('devopsCreateDrawer');
        if (!drawer || !drawer.classList.contains('open')) return;

        // If user is pasting inside an input, textarea, or contenteditable editor (Tiptap), let the editor handle it
        const target = e.target;
        if (target.closest('.tiptap-editor-element') || target.closest('.tiptap-wrapper') || target.isContentEditable || ['input', 'textarea'].includes(target.tagName?.toLowerCase())) {
            return;
        }

        const items = (e.clipboardData || window.clipboardData).items;
        if (!items) return;

        for (let i = 0; i < items.length; i++) {
            if (items[i].type.indexOf('image') !== -1) {
                const blob = items[i].getAsFile();
                if (blob) {
                    const nowStr = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
                    const file = new File([blob], `screenshot_${nowStr}.png`, { type: blob.type });
                    addDevOpsAttachment(file);
                    showToast('📷 Screenshot attached to work item files', false);
                    e.preventDefault();
                    return;
                }
            }
        }
    });

    // 3. Setup Draggable Left Resize Handle for DevOps Drawer
    setupDevOpsDrawerResizer();

    // 4. Tiptap Image Interaction Click Handler
    document.addEventListener('click', (e) => {
        const toolbar = document.getElementById('tiptapImageToolbar');
        if (!toolbar) return;

        if (e.target && e.target.tagName === 'IMG' && e.target.closest('.tiptap-editor-element')) {
            const img = e.target;
            activeTiptapImage = img;
            
            // Remove active class from all other images
            document.querySelectorAll('.tiptap-editor-element img.tiptap-image-active').forEach(el => el.classList.remove('tiptap-image-active'));
            img.classList.add('tiptap-image-active');

            // Position toolbar directly above the clicked image
            const rect = img.getBoundingClientRect();
            toolbar.style.top = `${rect.top + window.scrollY - 10}px`;
            toolbar.style.left = `${rect.left + window.scrollX + (rect.width / 2)}px`;
            toolbar.classList.remove('d-none');
            
            // Highlight active width preset if any
            const curWidth = img.style.width || '100%';
            toolbar.querySelectorAll('.tiptap-img-btn[data-width]').forEach(btn => {
                if (btn.getAttribute('data-width') === curWidth) {
                    btn.classList.add('active');
                } else {
                    btn.classList.remove('active');
                }
            });
        } else if (!e.target.closest('#tiptapImageToolbar')) {
            toolbar.classList.add('d-none');
            if (activeTiptapImage) {
                activeTiptapImage.classList.remove('tiptap-image-active');
                activeTiptapImage = null;
            }
        }
    });
});

// Draggable Sidebar Drawer Resizer
function setupDevOpsDrawerResizer() {
    const resizer = document.getElementById('devopsDrawerResizer');
    const drawer = document.getElementById('devopsCreateDrawer');
    if (!resizer || !drawer) return;

    let isDragging = false;

    resizer.addEventListener('mousedown', (e) => {
        isDragging = true;
        resizer.classList.add('is-resizing');
        document.body.style.userSelect = 'none';
        document.body.style.cursor = 'ew-resize';
        e.preventDefault();
    });

    window.addEventListener('mousemove', (e) => {
        if (!isDragging) return;
        const viewportWidth = window.innerWidth;
        let newWidth = viewportWidth - e.clientX;
        const minWidth = 440;
        const maxWidth = Math.floor(viewportWidth * 0.92);

        if (newWidth < minWidth) newWidth = minWidth;
        if (newWidth > maxWidth) newWidth = maxWidth;

        drawer.style.width = `${newWidth}px`;
        localStorage.setItem('devopsDrawerWidth', newWidth);
    });

    window.addEventListener('mouseup', () => {
        if (isDragging) {
            isDragging = false;
            resizer.classList.remove('is-resizing');
            document.body.style.userSelect = '';
            document.body.style.cursor = '';
        }
    });
}

// Inline Image Resizing & Alignment Toolbar Actions
function setTiptapImageWidth(width) {
    if (!activeTiptapImage) return;
    if (width === 'auto') {
        activeTiptapImage.style.width = 'auto';
        activeTiptapImage.style.maxWidth = '100%';
    } else {
        activeTiptapImage.style.width = width;
        activeTiptapImage.style.maxWidth = '100%';
        activeTiptapImage.style.height = 'auto';
    }
    
    const toolbar = document.getElementById('tiptapImageToolbar');
    if (toolbar) {
        toolbar.querySelectorAll('.tiptap-img-btn[data-width]').forEach(btn => {
            if (btn.getAttribute('data-width') === width) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        });
        // Reposition toolbar to match new dimensions
        const rect = activeTiptapImage.getBoundingClientRect();
        toolbar.style.top = `${rect.top + window.scrollY - 10}px`;
        toolbar.style.left = `${rect.left + window.scrollX + (rect.width / 2)}px`;
    }
}

function setTiptapImageAlign(align) {
    if (!activeTiptapImage) return;
    if (align === 'center') {
        activeTiptapImage.style.display = 'block';
        activeTiptapImage.style.marginLeft = 'auto';
        activeTiptapImage.style.marginRight = 'auto';
    } else if (align === 'right') {
        activeTiptapImage.style.display = 'block';
        activeTiptapImage.style.marginLeft = 'auto';
        activeTiptapImage.style.marginRight = '0';
    } else {
        activeTiptapImage.style.display = 'block';
        activeTiptapImage.style.marginLeft = '0';
        activeTiptapImage.style.marginRight = 'auto';
    }
}

function deleteTiptapActiveImage() {
    if (!activeTiptapImage) return;
    const toolbar = document.getElementById('tiptapImageToolbar');
    if (toolbar) toolbar.classList.add('d-none');
    activeTiptapImage.remove();
    activeTiptapImage = null;
    showToast('Image removed', false);
}
// Bilal Khan (31/08/2026) Issue No  Sheet_Name  - DevOps Media Attachments & Inline Image Resizer Logic - end

// Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Standardized DevOps Create Drawer Opening and Dropdown Closing - start
async function openDevOpsCreateDrawer(btn = null) {
    // 1. Always close the DevOps dropdown menu immediately
    toggleDevOpsDropdown(null, false);

    const drawer = document.getElementById('devopsCreateDrawer');
    if (!drawer) return;

    // 2. Immediate Button Visual Feedback
    let origBtnContent = '';
    if (btn) {
        origBtnContent = btn.innerHTML;
        btn.innerHTML = '<i class="fa fa-spinner fa-spin"></i> Opening...';
        btn.disabled = true;
    }

    // 3. Restore user preferred drawer width if previously resized
    const savedWidth = localStorage.getItem('devopsDrawerWidth');
    if (savedWidth) {
        drawer.style.width = `${savedWidth}px`;
    }

    // 4. Pre-fill Title from ticket subject
    const subjectEl = document.getElementById('modal-subject');
    const titleInput = document.getElementById('devopsTitleInput');
    if (titleInput && subjectEl) {
        titleInput.value = subjectEl.textContent.trim();
    }

    // 5. Pre-fill Description into Tiptap
    const activeTicket = currentTickets.find(t => t.id === currentTicketRow || t.ticket_id === currentTicketIdStr);
    const descHtml = activeTicket ? `
        <p><strong>Customer Reported:</strong></p>
        <p><strong>Ticket ID:</strong> ${escapeHtml(activeTicket.display_id || activeTicket.ticket_id)}<br>
        <strong>Customer:</strong> ${escapeHtml(activeTicket.customer_email || '')}<br>
        <strong>Subject:</strong> ${escapeHtml(activeTicket.subject || '')}</p>
        <p></p>
    ` : '';
    if (devopsDescEditor) {
        devopsDescEditor.commands.setContent(descHtml);
    }
    if (devopsReproEditor) {
        devopsReproEditor.commands.setContent('');
    }

    // 6. Pre-fill Tags
    const tagsInput = document.getElementById('devopsTagsInput');
    if (tagsInput) {
        tagsInput.value = 'support, customer-reported';
    }

    // 7. Reset attachments
    devopsPendingAttachments = [];
    renderDevOpsAttachmentPreviews();
    populateDevOpsTicketChips();

    // 8. Placeholders for Selects while loading
    const projSelect = document.getElementById('devopsProjectSelect');
    const userSelect = document.getElementById('devopsAssignedSelect');
    const iterSelect = document.getElementById('devopsIterationSelect');
    if (projSelect && !projSelect.value) projSelect.innerHTML = '<option value="">⏳ Loading projects...</option>';
    if (userSelect && !userSelect.value) userSelect.innerHTML = '<option value="">⏳ Loading team...</option>';
    if (iterSelect && !iterSelect.value) iterSelect.innerHTML = '<option value="">⏳ Loading sprints...</option>';

    // 9. INSTANT DRAWER OPENING (0ms perceived lag)
    onDevOpsTypeChange();
    drawer.classList.add('open');

    // 10. Fetch and populate Projects & Metadata asynchronously
    try {
        const pResp = await fetch('/api/devops/projects');
        if (pResp.ok) {
            const pData = await pResp.json();
            if (projSelect) {
                const projects = pData.projects || [];
                const defProj = pData.default_project || (projects[0] ? projects[0].name : '');
                
                if (projects.length > 0) {
                    projSelect.innerHTML = projects.map(p => 
                        `<option value="${escapeHtml(p.name)}" ${p.name === defProj ? 'selected' : ''}>${escapeHtml(p.name)}</option>`
                    ).join('');
                } else if (defProj) {
                    projSelect.innerHTML = `<option value="${escapeHtml(defProj)}" selected>${escapeHtml(defProj)}</option>`;
                }
                await onDevOpsProjectChange(projSelect.value);
            }
        }
    } catch (e) {
        console.error('Failed to load DevOps projects:', e);
    } finally {
        if (btn) {
            btn.innerHTML = origBtnContent;
            btn.disabled = false;
        }
    }
}
// Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Standardized DevOps Create Drawer Opening and Dropdown Closing - end

async function onDevOpsProjectChange(projectName) {
    const project = projectName || (document.getElementById('devopsProjectSelect') ? document.getElementById('devopsProjectSelect').value : '');
    let metaUrl = '/api/devops/meta';
    if (project) {
        metaUrl = `/api/devops/meta/${encodeURIComponent(project)}`;
    }

    try {
        const resp = await fetch(metaUrl);
        if (resp.ok) {
            const meta = await resp.json();
            
            // Types
            const typeSelect = document.getElementById('devopsTypeSelect');
            if (typeSelect && meta.work_item_types && meta.work_item_types.length > 0) {
                const curVal = typeSelect.value || 'Bug';
                typeSelect.innerHTML = meta.work_item_types.map(t => {
                    const icon = t === 'Bug' ? '🪲' : (t === 'User Story' ? '📖' : '📋');
                    return `<option value="${t}" ${t === curVal ? 'selected' : ''}>${icon} ${t}</option>`;
                }).join('');
            }

            // Users
            const userSelect = document.getElementById('devopsAssignedSelect');
            if (userSelect && meta.users) {
                userSelect.innerHTML = '<option value="">Unassigned</option>' + meta.users.map(u => 
                    `<option value="${escapeHtml(u.uniqueName || u.displayName)}">${escapeHtml(u.displayName)}</option>`
                ).join('');
            }

            // Area Paths
            const areaSelect = document.getElementById('devopsAreaSelect');
            if (areaSelect) {
                areaSelect.innerHTML = '<option value="">Default Area</option>' + (meta.area_paths || []).map(a => 
                    `<option value="${escapeHtml(a)}">${escapeHtml(a)}</option>`
                ).join('');
            }

            // Iteration Paths (Auto-select active sprint as suggestive default)
            const iterSelect = document.getElementById('devopsIterationSelect');
            if (iterSelect) {
                const activeIter = meta.active_iteration || '';
                iterSelect.innerHTML = '<option value="">Default Iteration</option>' + (meta.iteration_paths || []).map(i => {
                    const isSelected = activeIter && (i === activeIter || i.endsWith('\\' + activeIter) || activeIter.endsWith('\\' + i));
                    return `<option value="${escapeHtml(i)}" ${isSelected ? 'selected' : ''}>${escapeHtml(i)}</option>`;
                }).join('');
            }
        }
    } catch (e) {
        console.error('Failed to load project metadata:', e);
    }
}

function closeDevOpsCreateDrawer() {
    const drawer = document.getElementById('devopsCreateDrawer');
    if (drawer) drawer.classList.remove('open');
    devopsPendingAttachments = [];
    renderDevOpsAttachmentPreviews();
    if (devopsDescEditor) devopsDescEditor.commands.setContent('');
    if (devopsReproEditor) devopsReproEditor.commands.setContent('');
}

function onDevOpsTypeChange() {
    const typeSelect = document.getElementById('devopsTypeSelect');
    const bugFields = document.getElementById('devopsBugFields');
    if (!typeSelect || !bugFields) return;

    const isBug = typeSelect.value.toLowerCase() === 'bug';
    bugFields.style.display = isBug ? 'block' : 'none';
}

// Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Dynamic DevOps User Typeahead Autocomplete - start
let devopsUserSearchTimer = null;

async function onDevOpsUserSearch(query) {
    const suggestions = document.getElementById('devopsAssignedSuggestions');
    if (!suggestions) return;

    if (devopsUserSearchTimer) clearTimeout(devopsUserSearchTimer);

    devopsUserSearchTimer = setTimeout(async () => {
        try {
            const resp = await fetch(`/api/devops/users?q=${encodeURIComponent(query || '')}`);
            if (resp.ok) {
                const data = await resp.json();
                const users = data.users || [];
                
                let html = `
                    <div class="autocomplete-item p-2" style="cursor: pointer; border-bottom: 1px solid var(--border-color);" onclick="selectDevOpsAssignedUser('', 'Unassigned')">
                        <span class="text-muted">👤 Unassigned</span>
                    </div>
                `;

                if (users.length > 0) {
                    html += users.map(u => `
                        <div class="autocomplete-item p-2 d-flex flex-column" style="cursor: pointer; border-bottom: 1px solid var(--border-color);" onclick="selectDevOpsAssignedUser('${escapeHtml(u.uniqueName || u.displayName)}', '${escapeHtml(u.displayName)}')">
                            <span class="fw-bold small" style="color: var(--text-primary);">👤 ${escapeHtml(u.displayName)}</span>
                            <span class="text-muted" style="font-size: 0.72rem;">${escapeHtml(u.mailAddress || u.uniqueName || '')}</span>
                        </div>
                    `).join('');
                } else if (query) {
                    html += `<div class="p-2 text-muted small">No matching team members found</div>`;
                }

                suggestions.innerHTML = html;
                suggestions.classList.remove('d-none');
            }
        } catch (e) {
            console.error('Error searching DevOps users:', e);
        }
    }, 200);
}

function selectDevOpsAssignedUser(val, display) {
    const input = document.getElementById('devopsAssignedInput');
    const hiddenVal = document.getElementById('devopsAssignedValue');
    const suggestions = document.getElementById('devopsAssignedSuggestions');

    if (input) input.value = display === 'Unassigned' ? '' : display;
    if (hiddenVal) hiddenVal.value = val;
    if (suggestions) suggestions.classList.add('d-none');
}

// Close DevOps user suggestions when clicking outside
document.addEventListener('click', (e) => {
    const input = document.getElementById('devopsAssignedInput');
    const suggestions = document.getElementById('devopsAssignedSuggestions');
    if (suggestions && input && !input.contains(e.target) && !suggestions.contains(e.target)) {
        suggestions.classList.add('d-none');
    }
});
// Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Dynamic DevOps User Typeahead Autocomplete - end

async function submitDevOpsCreate(e) {
    e.preventDefault();
    if (!currentTicketIdStr) return;

    const btn = document.getElementById('btnSubmitDevOpsCreate');
    const originalText = btn ? btn.innerHTML : 'Create';
    const projSelect = document.getElementById('devopsProjectSelect');
    const targetProject = projSelect ? projSelect.value : '';

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Creating...';
    }

    try {
        // 1. Upload Pending Attachments directly to Azure DevOps
        const uploadedAttachmentUrls = [];
        if (devopsPendingAttachments.length > 0) {
            if (btn) btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>Uploading Media (${devopsPendingAttachments.length})...`;

            for (let i = 0; i < devopsPendingAttachments.length; i++) {
                const att = devopsPendingAttachments[i];
                if (att.uploadedUrl) {
                    uploadedAttachmentUrls.push(att.uploadedUrl);
                } else if (att.file) {
                    const formData = new FormData();
                    formData.append('file', att.file);
                    formData.append('project', targetProject);

                    const upResp = await fetch('/api/devops/upload_attachment', {
                        method: 'POST',
                        body: formData
                    });

                    if (upResp.ok) {
                        const upData = await upResp.json();
                        if (upData.url) {
                            uploadedAttachmentUrls.push(upData.url);
                        }
                    } else {
                        console.warn(`Failed to upload attachment ${att.name} to DevOps`);
                    }
                }
            }
        }

        if (btn) btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Creating Work Item...';

        // 2. Extract HTML contents from Tiptap editors
        const descContent = devopsDescEditor ? devopsDescEditor.getHTML() : '';
        const reproContent = devopsReproEditor ? devopsReproEditor.getHTML() : '';

        // 3. Extract Assigned User value
        const assignedVal = document.getElementById('devopsAssignedValue') ? document.getElementById('devopsAssignedValue').value : '';
        const assignedInput = document.getElementById('devopsAssignedInput') ? document.getElementById('devopsAssignedInput').value.trim() : '';
        const finalAssigned = assignedVal || assignedInput;

        // 4. Create the Work Item with linked attachment URLs
        const payload = {
            ticket_id: currentTicketIdStr,
            project: targetProject,
            type: document.getElementById('devopsTypeSelect').value,
            title: document.getElementById('devopsTitleInput').value.trim(),
            description: descContent,
            assigned_to: finalAssigned,
            area_path: document.getElementById('devopsAreaSelect').value,
            iteration_path: document.getElementById('devopsIterationSelect').value,
            priority: document.getElementById('devopsPrioritySelect').value,
            tags: document.getElementById('devopsTagsInput').value.trim(),
            attachment_urls: uploadedAttachmentUrls
        };

        if (payload.type.toLowerCase() === 'bug') {
            payload.severity = document.getElementById('devopsSeveritySelect').value;
            payload.repro_steps = reproContent;
            payload.system_info = document.getElementById('devopsSystemInfoInput').value.trim();
        }

        const resp = await fetch('/api/devops/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await resp.json();
        if (resp.ok && data.success) {
            showToast(`Created & Linked Work Item #${data.work_item.id} with ${uploadedAttachmentUrls.length} attachment(s)`, false);
            closeDevOpsCreateDrawer();
            loadLinkedDevOpsItems(currentTicketIdStr);
        } else {
            alert(data.error || 'Failed to create work item in Azure DevOps');
        }
    } catch (err) {
        console.error('Error creating DevOps item:', err);
        alert('Network error while creating work item in Azure DevOps');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }
}
// Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Azure DevOps Multi-Project and Active Sprint Logic - end

async function closeTicketDialog() {
    if (!currentTicketRow) return;
    if (!confirm('Are you sure you want to close this ticket?')) return;

    try {
        const response = await fetch('/api/close_ticket', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                row_number: currentTicketRow,
                ticket_id: currentTicketIdStr
            })
        });

        if (response.ok) {
            closeModal();
            loadTickets();
        }
    } catch (error) {
        console.error('Error closing ticket:', error);
    }
}

function closeTimeline() {
    const modal = document.getElementById('timelineModal');
    if (modal) modal.classList.remove('active');
}

function showTimeline(ticketId, subject, displayId = '') {
    const infoEl = document.getElementById('timelineTicketInfo');
    if (infoEl) infoEl.textContent = `Ticket: ${subject} (#${displayId || truncateTicketId(ticketId)})`;

    const listEl = document.getElementById('timelineList');
    if (listEl) listEl.innerHTML = '<p class="text-center py-4">Loading timeline...</p>';

    openModal('timelineModal');
    socket.emit('get_ticket_timeline', { ticket_id: ticketId });
}

function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) modal.classList.add('active');
}

window.onTimeRangeChange = function() {
    const range = document.getElementById('timeRangeSelect').value;
    currentTimeRange = range;
    const customInputs = document.getElementById('customDateInputs');
    if (range === 'custom') {
        customInputs.classList.remove('d-none');
        customInputs.classList.add('d-flex');
    } else {
        customInputs.classList.remove('d-flex');
        customInputs.classList.add('d-none');
        displayTickets();
    }
};

window.applyCustomDates = function() {
    customFromDate = document.getElementById('fromDateInput').value;
    customToDate = document.getElementById('toDateInput').value;
    displayTickets();
};

// Initial setup
document.addEventListener('DOMContentLoaded', async () => {
    const savedTheme = localStorage.getItem('darkMode') === 'true';
    if (savedTheme) {
        document.body.classList.add('dark-mode');
        updateThemeButton(true);
    }

    const attachmentInput = document.getElementById('attachmentInput');
    if (attachmentInput) {
        attachmentInput.addEventListener('change', (e) => {
            const files = Array.from(e.target.files);
            attachedFiles = [...attachedFiles, ...files];
            updateAttachedFilesList();
        });
    }

    // Initialize Flatpickr date pickers
    if (typeof flatpickr !== 'undefined') {
        flatpickr("#fromDateInput", {
            dateFormat: "d-m-Y",
            allowInput: true
        });
        flatpickr("#toDateInput", {
            dateFormat: "d-m-Y",
            allowInput: true
        });
    }

    // Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Initialize Tiptap v2 Editor - start
    initTiptapEditor();
    // Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Initialize Tiptap v2 Editor - end

    await fetchContacts();
    setupAutocomplete('emailTo', 'suggestionsTo');
    setupAutocomplete('emailCC', 'suggestionsCC');
    setupAutocomplete('emailBCC', 'suggestionsBCC');

    await loadTickets();

    // Parse filters from URL query parameters (Management Console redirect)
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.has('status')) {
        const status = urlParams.get('status');
        currentFilter = normalizeStatus(status);
    }
    document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.filter === currentFilter);
    });
    if (urlParams.has('assignee')) {
        setAssignmentFilter(urlParams.get('assignee'));
    }
    if (urlParams.has('domain')) {
        setCustomerFilter(urlParams.get('domain'));
    }
    if (urlParams.has('search')) {
        const searchInput = document.getElementById('search');
        if (searchInput) {
            searchInput.value = urlParams.get('search');
        }
    }
    if (urlParams.has('time_range')) {
        const timeRange = urlParams.get('time_range');
        const select = document.getElementById('timeRangeSelect');
        if (select) {
            select.value = timeRange;
            currentTimeRange = timeRange;
            onTimeRangeChange();
        }
    }
    if (urlParams.has('from') && urlParams.has('to')) {
        const select = document.getElementById('timeRangeSelect');
        if (select) {
            select.value = 'custom';
            currentTimeRange = 'custom';
            onTimeRangeChange();
        }
        const fromInput = document.getElementById('fromDateInput');
        const toInput = document.getElementById('toDateInput');
        if (fromInput && toInput) {
            fromInput.value = urlParams.get('from');
            toInput.value = urlParams.get('to');
            customFromDate = urlParams.get('from');
            customToDate = urlParams.get('to');
        }
    }

    const pillContainer = document.getElementById('visibilityPill');
    if (pillContainer) {
        pillContainer.setAttribute('data-active', currentVisibility);
        document.querySelectorAll('.pill-option').forEach(o => {
            o.classList.toggle('active', o.dataset.visibility === currentVisibility);
        });
    }
    displayTickets();

    document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.addEventListener('click', function () {
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            this.classList.add('active');
            currentFilter = this.dataset.filter;
            displayTickets();
        });
    });

    document.querySelectorAll('.pill-option').forEach(opt => {
        opt.addEventListener('click', function () {
            const container = document.getElementById('visibilityPill');
            const visibility = this.dataset.visibility;
            document.querySelectorAll('.pill-option').forEach(o => o.classList.remove('active'));
            this.classList.add('active');
            container.setAttribute('data-active', visibility);
            currentVisibility = visibility;
            displayTickets();
        });
    });

    const searchInput = document.getElementById('search');
    if (searchInput) {
        searchInput.addEventListener('input', displayTickets);
    }
});

// Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Refresh Button and Relative Time Tooltip Logic - start
let lastRefreshedTimestamp = Date.now();

function getRelativeRefreshTimeString() {
    if (!lastRefreshedTimestamp) return 'Refreshed just now';
    const diffSeconds = Math.max(0, Math.floor((Date.now() - lastRefreshedTimestamp) / 1000));
    
    if (diffSeconds < 5) {
        return 'Refreshed just now';
    } else if (diffSeconds < 60) {
        return `Refreshed ${diffSeconds} second${diffSeconds === 1 ? '' : 's'} ago`;
    }
    
    const diffMinutes = Math.floor(diffSeconds / 60);
    if (diffMinutes < 60) {
        return `Refreshed ${diffMinutes} min${diffMinutes === 1 ? '' : 's'} ago`;
    }
    
    const diffHours = Math.floor(diffMinutes / 60);
    if (diffHours < 24) {
        return `Refreshed ${diffHours} hour${diffHours === 1 ? '' : 's'} ago`;
    }
    
    const diffDays = Math.floor(diffHours / 24);
    return `Refreshed ${diffDays} day${diffDays === 1 ? '' : 's'} ago`;
}

function updateRefreshTooltip() {
    const btn = document.getElementById('btnRefreshTickets');
    if (btn) {
        btn.setAttribute('title', getRelativeRefreshTimeString());
    }
}

async function manualRefreshTickets() {
    const icon = document.getElementById('refreshIcon');
    const indicator = document.getElementById('refreshIndicator');
    
    if (icon) icon.classList.add('fa-spin');
    if (indicator) indicator.classList.add('show');
    
    try {
        await loadTickets();
        lastRefreshedTimestamp = Date.now();
        updateRefreshTooltip();
    } finally {
        setTimeout(() => {
            if (icon) icon.classList.remove('fa-spin');
            if (indicator) indicator.classList.remove('show');
        }, 600);
    }
}

// Bind hover event listener to dynamically refresh relative time on hover
document.addEventListener('DOMContentLoaded', () => {
    const refreshBtn = document.getElementById('btnRefreshTickets');
    if (refreshBtn) {
        refreshBtn.addEventListener('mouseenter', updateRefreshTooltip);
    }
});

// Auto-refresh (every 30 seconds)
setInterval(() => {
    const indicator = document.getElementById('refreshIndicator');
    if (indicator) {
        indicator.classList.add('show');
        loadTickets();
        setTimeout(() => indicator.classList.remove('show'), 2000);
    }
}, 30000);
// Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Refresh Button and Relative Time Tooltip Logic - end

// Socket events
socket.on('connect', () => {
    console.log('✓ Connected to server');
    socket.emit('refresh_tickets');
});

socket.on('stats_update', (stats) => {
    updateStats(stats);
});

socket.on('ticket_updated', () => loadTickets());
socket.on('ticket_status_toggled', () => loadTickets());
socket.on('email_sent', () => loadTickets());

// Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Azure DevOps Real-time State Update Socket Listener - start
socket.on('devops_item_updated', (data) => {
    if (!data) return;
    const { ticket_id, work_item_id, old_state, new_state } = data;
    
    // If ticket modal is open for this ticket, refresh linked items
    if (currentTicketIdStr && String(currentTicketIdStr) === String(ticket_id)) {
        loadLinkedDevOpsItems(currentTicketIdStr);
    }
    
    showToast(`DevOps #${work_item_id}: ${old_state} ➔ ${new_state}`, false);
});
// Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Azure DevOps Real-time State Update Socket Listener - end

// Global state for audit logs
let cachedAuditLogs = [];
let auditGrid = null;

// Handle timeline updates
socket.on('ticket_timeline_update', (data) => {
    cachedAuditLogs = data.timeline || [];
    renderAuditLogs();
    calculateAndDisplaySLA(cachedAuditLogs);
});

function renderAuditLogs() {
    const gridContainer = document.getElementById('auditGrid');
    const emptyState = document.getElementById('auditEmptyState');
    if (!gridContainer) return;

    const internalOnly = document.getElementById('showInternalOnly').checked;

    // Deduplicate logs (noise reduction)
    const uniqueLogs = [];
    const seenEvents = new Set();
    cachedAuditLogs.forEach(ev => {
        const key = `${ev.timestamp}-${ev.action_type}-${ev.actor_id}`;
        if (!seenEvents.has(key)) {
            uniqueLogs.push(ev);
            seenEvents.add(key);
        }
    });

    const filteredData = uniqueLogs.filter(ev => {
        if (!internalOnly) {
            return !['AI_DRAFT_GENERATED', 'NOTE_ADDED', 'AI_DRAFT_UPDATED'].includes(ev.action_type);
        }
        return true;
    }).map(ev => {
        const ts = new Date(ev.timestamp);
        return [
            ts.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }),
            ts.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: true }),
            ev.action_type,
            ev.actor_id || 'System',
            ev.event_text || ev.description
        ];
    });

    if (filteredData.length === 0) {
        gridContainer.classList.add('d-none');
        emptyState.classList.remove('d-none');
        return;
    }

    gridContainer.classList.remove('d-none');
    emptyState.classList.add('d-none');

    if (auditGrid) auditGrid.destroy();

    const columns = [
        { name: 'Date', width: '120px', sort: true },
        { name: 'Time', width: '100px', sort: true },
        {
            name: 'Action',
            width: '120px',
            formatter: (cell) => {
                let badgeClass = 'badge-ai';
                let label = cell.replace('_', ' ').toUpperCase();
                if (cell === 'MESSAGE_RECEIVED') { badgeClass = 'badge-received'; label = 'Received'; }
                else if (cell === 'MESSAGE_SENT') { badgeClass = 'badge-sent'; label = 'Sent'; }
                else if (cell === 'STATUS_CHANGED') { badgeClass = 'badge-status'; label = 'Status'; }
                else if (cell === 'ASSIGNMENT_CHANGED') { badgeClass = 'badge-assign'; label = 'Assign'; }
                else if (cell === 'NOTE_ADDED') { badgeClass = 'badge-assign'; label = 'Note'; }

                return gridjs.html(`<span class="badge-audit ${badgeClass}">${label}</span>`);
            }
        },
        {
            name: 'Actor',
            width: '150px',
            formatter: (cell) => gridjs.html(`<div class="fw-bold text-truncate" title="${cell}">${cell}</div>`)
        },
        { name: 'Description' }
    ];

    // Metadata column removed as per user request

    auditGrid = new gridjs.Grid({
        columns: columns,
        data: filteredData,
        search: true,
        sort: true,
        pagination: { limit: 10 },
        className: { table: 'table table-hover mb-0' }
    }).render(gridContainer);
}

// Global helper for metadata popup (Grid.js context doesn't handle inline toggles well without complex plugins)
window.showMetadataPopup = (meta) => {
    const metaStr = JSON.stringify(meta, null, 4);
    alert("Metadata Details:\n\n" + metaStr); // Simplified for now, can be a Toast or another Modal
};

function calculateAndDisplaySLA(logs) {
    let createdTime = null;
    let firstResponseTime = null;
    let resolvedTime = null;
    let totalWait = 0;
    let waitStart = null;

    // Setting check: Internal Note as Response
    const internalNoteAsResponse = globalConfig.INTERNAL_NOTE_AS_RESPONSE === 'true' || globalConfig.INTERNAL_NOTE_AS_RESPONSE === true;

    logs.forEach(ev => {
        const ts = new Date(ev.timestamp);
        if (ev.action_type === 'TICKET_CREATED') createdTime = ts;

        // Handle response tracking
        const isOfficialResponse = ev.action_type === 'MESSAGE_SENT';
        const isNoteAsResponse = internalNoteAsResponse && ev.action_type === 'NOTE_ADDED';

        if ((isOfficialResponse || isNoteAsResponse) && (!firstResponseTime || ts < firstResponseTime)) {
            firstResponseTime = ts;
        }

        if (ev.action_type === 'STATUS_CHANGED' && (ev.metadata?.to === 'Closed' || ev.metadata?.to === 'Resolved')) {
            resolvedTime = ts;
        }

        if (ev.action_type === 'MESSAGE_RECEIVED') waitStart = ts;
        if (isOfficialResponse && waitStart) {
            totalWait += (ts - waitStart);
            waitStart = null;
        }
    });

    const update = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };

    if (createdTime) {
        if (firstResponseTime) {
            const diff = Math.round((firstResponseTime - createdTime) / 60000);
            update('sla-frt', diff + 'm');
            const badge = document.getElementById('sla-status');
            if (badge) {
                badge.textContent = diff < 240 ? 'SLA Met' : 'SLA Breached';
                badge.className = 'sla-status-badge ' + (diff < 240 ? 'met' : 'breached');
            }
        } else update('sla-frt', '-');

        if (resolvedTime) {
            update('sla-res', ((resolvedTime - createdTime) / 3600000).toFixed(1) + 'h');
        } else update('sla-res', '-');

        update('sla-wait', (totalWait / 3600000).toFixed(1) + 'h');
    }
}

function exportTimeline() {
    const internalOnly = document.getElementById('showInternalOnly').checked;

    const toExport = cachedAuditLogs.filter(ev => {
        if (!internalOnly) {
            return !['AI_DRAFT_GENERATED', 'NOTE_ADDED', 'AI_DRAFT_UPDATED'].includes(ev.action_type);
        }
        return true;
    });

    if (toExport.length === 0) return alert("No filtered data to export");

    const headers = ["Date", "Time", "Action", "Actor", "Description"];
    if (internalOnly) headers.push("Metadata");

    const rows = toExport.map(ev => {
        const ts = new Date(ev.timestamp);
        const row = [
            ts.toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit', year: 'numeric' }).replace(/\//g, '-'),
            ts.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true }),
            ev.action_type,
            ev.actor_id,
            ev.event_text || ev.description || ""
        ];
        if (internalOnly) {
            row.push(JSON.stringify(ev.metadata || {}));
        }
        return row;
    });

    let csvContent = "data:text/csv;charset=utf-8,"
        + headers.map(h => `"${h}"`).join(",") + "\n"
        + rows.map(row => row.map(cell => `"${(cell || "").toString().replace(/"/g, '""')}"`).join(",")).join("\n");

    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `ticket_${currentTicketIdStr || 'logs'}_audit.csv`);
    document.body.appendChild(link); // Required for FF
    link.click();
    document.body.removeChild(link);
}

socket.on('ticket_locked', (data) => {
    const card = document.querySelector(`.ticket-card[data-ticket-id="${data.ticket_id}"]`);
    if (card) card.classList.add('locked-by-other');

    if (currentTicketIdStr === data.ticket_id && data.locked_by !== 'me') {
        const lockStatus = document.getElementById('lockStatus');
        if (lockStatus) {
            lockStatus.textContent = `🔒 Locked by ${data.locked_by}`;
            lockStatus.classList.add('active');
        }
        if (quillEditor) quillEditor.disable();
        const btnSave = document.getElementById('btnSave');
        const btnSend = document.getElementById('btnSend');
        const statusSelect = document.getElementById('modal-status');
        if (btnSave) btnSave.disabled = true;
        if (btnSend) btnSend.disabled = true;
        if (statusSelect) statusSelect.disabled = true;
        isTicketLocked = true;
    }
});

socket.on('ticket_unlocked', (data) => {
    const card = document.querySelector(`.ticket-card[data-ticket-id="${data.ticket_id}"]`);
    if (card) card.classList.remove('locked-by-other');

    if (currentTicketIdStr === data.ticket_id) {
        const lockStatus = document.getElementById('lockStatus');
        if (lockStatus) lockStatus.classList.remove('active');
        if (quillEditor) quillEditor.enable();
        const btnSave = document.getElementById('btnSave');
        const btnSend = document.getElementById('btnSend');
        const statusSelect = document.getElementById('modal-status');
        if (btnSave) btnSave.disabled = false;
        if (btnSend) btnSend.disabled = false;
        if (statusSelect) statusSelect.disabled = false;
        isTicketLocked = false;
        socket.emit('lock_ticket', { ticket_id: currentTicketIdStr });
    }
});