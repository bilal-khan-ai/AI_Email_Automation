
let currentTickets = [];
let currentFilter = 'all';
let currentVisibility = 'all';
let currentTicketRow = null;
let currentTicketIdStr = null;
let quillEditor = null;
let attachedFiles = [];
let currentAssignmentFilter = null;
let currentCustomerFilter = null;
let isTicketLocked = false;
let isRightPanelCollapsed = true;
let currentTicketStatus = null; // Added this as it was used but not declared globally in the snippet (or was it?)
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
    } else if (statusLower === 'pending review' || statusLower === 'pending' || statusLower === 'open' || statusLower === 'in progress') {
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

    let filteredTickets = processedTickets;

    if (currentVisibility === 'mine') {
        filteredTickets = filteredTickets.filter(t => t.assigned_to === currentUsername);
    }

    if (typeof currentFilter !== 'undefined' && currentFilter !== 'all') {
        filteredTickets = filteredTickets.filter(t => normalizeStatus(t.status) === currentFilter);
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

    let statsBase = processedTickets;
    if (currentVisibility === 'mine') {
        statsBase = statsBase.filter(t => t.assigned_to === currentUsername);
    }

    const dynamicStats = {
        total: statsBase.length,
        open: statsBase.filter(t => normalizeStatus(t.status) === 'Open').length,
        closed: statsBase.filter(t => normalizeStatus(t.status) === 'Closed').length
    };
    updateStats(dynamicStats);

    if (typeof populateAssignmentFilter === 'function') populateAssignmentFilter();
    if (typeof populateCustomerFilter === 'function') populateCustomerFilter();

    filteredTickets.sort((a, b) => b.last_message_at - a.last_message_at);

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

    const ticketCards = filteredTickets.map(ticket => {
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

        const unansweredBadgeHtml = showUnansweredBadge ?
            '<span class="sla-badge-unanswered">⏰ Unanswered 24h+</span>' : '';

        const importantBadgeHtml = isReopened ?
            '<span class="important-badge">🚩 Important</span>' : '';

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
                </div>
                <div style="display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center;">
                    <span class="ticket-status status-${statusClass}">${status}</span>
                    <span class="assignment-badge ${assignedTo === 'Unassigned' ? 'unassigned' : ''}">👤 ${assignedTo}</span>
                </div>
            </div>
            <div class="ticket-subject">${subject}</div>
            <div class="ticket-meta">
                <span>📧 ${customerEmail}</span>
                <span>📅 ${timestamp}</span>
            </div>
        </div>
        `;
    }).join('');

    container.innerHTML = ticketCards;
}

async function openTicket(ticketId, rowNumber) {
    currentTicketRow = rowNumber;
    currentTicketIdStr = ticketId;

    const ticket = currentTickets.find(t => t.id === rowNumber || t.ticket_id === ticketId);
    if (!ticket) return;

    currentTicketStatus = normalizeStatus(ticket.status);

    initializeEditor();
    attachedFiles = [];
    updateAttachedFilesList();

    const rightPanel = document.getElementById('rightPanel');
    isRightPanelCollapsed = true;
    rightPanel.classList.add('collapsed');

    if (testMode) {
        document.getElementById('emailTo').value = testEmail;
        document.getElementById('emailTo').readOnly = true;
        document.getElementById('emailTo').classList.add('test-mode-input');
        document.getElementById('emailTo').title = "Email redirected to test receiver in Test Mode";
    } else {
        document.getElementById('emailTo').value = ticket.customer_email || '';
        document.getElementById('emailTo').readOnly = false;
        document.getElementById('emailTo').classList.remove('test-mode-input');
        document.getElementById('emailTo').style.backgroundColor = "";
        document.getElementById('emailTo').style.cursor = "";
        document.getElementById('emailTo').title = "";
    }
    document.getElementById('modal-subject').textContent = ticket.subject || 'No Subject';
    document.getElementById('modal-ticket-id').textContent = ticket.display_id || truncateTicketId(ticket.ticket_id || 'NO-ID');
    document.getElementById('modal-ticket-id').title = ticket.ticket_id || 'NO-ID';
    document.getElementById('modal-email').textContent = ticket.customer_email || 'Unknown';
    document.getElementById('modal-date').textContent = formatDate(ticket.last_updated);
    document.getElementById('modal-assignment').value = ticket.assigned_to || '';
    document.body.classList.add('modal-open');

    if (ticket.ai_draft) {
        const sanitizedDraft = DOMPurify.sanitize(ticket.ai_draft, {
            ALLOWED_TAGS: [
                'p', 'br', 'strong', 'em', 'u', 's', 'a', 'ul', 'ol', 'li',
                'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'code', 'pre'
            ],
            ALLOWED_ATTR: ['href', 'target', 'rel', 'class'],
            ALLOW_DATA_ATTR: false,
            ALLOWED_URI_REGEXP: /^(?:(?:(?:f|ht)tps?|mailto|tel|callto|cid|xmpp):|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i
        });

        quillEditor.root.innerHTML = sanitizedDraft;
    } else {
        quillEditor.root.innerHTML = '';
    }

    updateToggleButton(currentTicketStatus);

    const lockStatus = document.getElementById('lockStatus');
    const btnSave = document.getElementById('btnSave');
    const btnSend = document.getElementById('btnSend');
    const btnToggle = document.getElementById('btnToggleStatus');

    if (ticket.locked_by) {
        isTicketLocked = true;
        lockStatus.textContent = `🔒 Locked by ${ticket.locked_by}`;
        lockStatus.classList.add('active');
        quillEditor.disable();
        btnSave.disabled = true;
        btnSend.disabled = true;
        btnToggle.disabled = true;
    } else {
        isTicketLocked = false;
        lockStatus.classList.remove('active');
        quillEditor.enable();
        btnSave.disabled = false;
        btnSend.disabled = false;
        btnToggle.disabled = false;

        if (socket && socket.connected) {
            socket.emit('lock_ticket', { ticket_id: ticketId });
        }
    }

    const emailChainContainer = document.getElementById('emailChainContainer');
    emailChainContainer.innerHTML = '';
    document.getElementById('threadLoading').style.display = 'block';
    document.getElementById('ticketModal').classList.add('active');

    try {
        const res = await fetch(`/api/ticket_messages/${ticketId}`);
        if (!res.ok) throw new Error("Failed to fetch messages");
        const data = await res.json();
        const messages = data.messages || [];

        document.getElementById('threadLoading').style.display = 'none';
        renderThread(messages);

        const emailCC = document.getElementById('emailCC');
        const emailBCC = document.getElementById('emailBCC');
        const ccField = document.getElementById('ccField');
        const bccField = document.getElementById('bccField');

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

    } catch (err) {
        console.error("Error fetching messages:", err);
        emailChainContainer.innerHTML = '<p style="color:red; padding:10px;">Error loading thread.</p>';
        document.getElementById('threadLoading').style.display = 'none';
    }
}

function updateToggleButton(status) {
    const toggleIcon = document.getElementById('toggleStatusIcon');
    const toggleText = document.getElementById('toggleStatusText');

    if (toggleIcon && toggleText) {
        if (status === 'Open') {
            toggleIcon.textContent = '✓';
            toggleText.textContent = 'Close Ticket';
        } else {
            toggleIcon.textContent = '↻';
            toggleText.textContent = 'Reopen Ticket';
        }
    }
}

async function toggleTicketStatus() {
    if (isTicketLocked) return;

    const action = currentTicketStatus === 'Open' ? 'close' : 'reopen';
    if (!confirm(`Are you sure you want to ${action} this ticket?`)) return;

    try {
        const response = await fetch('/api/toggle_ticket_status', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                row_number: currentTicketRow,
                ticket_id: currentTicketIdStr
            })
        });

        if (response.ok) {
            const data = await response.json();
            currentTicketStatus = data.new_status;
            updateToggleButton(currentTicketStatus);

            if (currentTicketStatus === 'Closed') {
                closeModal();
            }

            loadTickets();
        } else {
            alert('Failed to update ticket status.');
        }
    } catch (error) {
        console.error('Error toggling status:', error);
        alert('Error updating ticket status.');
    }
}

function escapeHtml(text) {
    if (!text) return "";
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function processCidImages(body, cidMap) {
    if (!body) return '';
    let newBody = body;

    if (!cidMap || Object.keys(cidMap).length === 0) return body;

    const escapeRegExp = (string) => string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

    Object.keys(cidMap).forEach(cid => {
        const att = cidMap[cid];
        const viewUrl = `${window.location.origin}/api/view_attachment?message_id=${encodeURIComponent(att.msgId)}&attachment_id=${encodeURIComponent(att.id)}&filename=${encodeURIComponent(att.name || 'image')}&content_type=${encodeURIComponent(att.ct || '')}`;

        const escapedCid = escapeRegExp(cid);
        const encodedCid = escapeRegExp(cid.replace('@', '%40'));

        const pattern = new RegExp(`cid:<?(${escapedCid}|{encodedCid})>?`, 'gi');
        newBody = newBody.replace(pattern, viewUrl);
    });

    return newBody;
}

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
        attList.forEach((att, idx) => {
            const attName = escapeHtml(att.name || 'file');
            const isImage = att.content_type && att.content_type.startsWith('image/');
            const isPDF = att.content_type === 'application/pdf' || attName.toLowerCase().endsWith('.pdf');
            const isPreviewable = isImage || isPDF;

            attachmentsHtml += `
                <div class="attachment-item" style="display: flex; align-items: center; justify-content: space-between; padding: 0.6rem 1rem; background: var(--bg-card); border-radius: 8px; margin-top: 0.4rem; font-size: 0.85rem; border: 1px solid var(--border-color); transition: all 0.2s;">
                    <div style="display: flex; align-items: center; gap: 0.75rem; flex: 1; overflow: hidden;">
                        <span style="font-size: 1.2rem;">${isImage ? '🖼️' : (isPDF ? '📕' : '📄')}</span>
                        <div style="display: flex; flex-direction: column; overflow: hidden;">
                            <span style="font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-primary);" title="${attName}">${attName}</span>
                            <span style="opacity:0.6; font-size: 0.75rem; color: var(--text-secondary);">${formatFileSize(att.size)} • ${escapeHtml(att.content_type || 'Unknown')}</span>
                        </div>
                    </div>
                    <div class="attachment-actions" style="margin-left: 1rem;">
                        ${isPreviewable ? `
                        <button 
                            class="attachment-preview-btn" 
                            data-att-id="${escapeHtml(att.id)}"
                            data-msg-id="${escapeHtml(messageId || '')}"
                            data-filename="${attName}"
                            data-type="${escapeHtml(att.content_type || '')}"
                            onclick="event.stopPropagation(); handlePreviewClick(this)">Preview</button>
                        ` : ''}
                        <button 
                            class="attachment-download-btn" 
                            onclick="event.stopPropagation(); downloadAttachment('${escapeHtml(att.id)}', '${escapeHtml(messageId || '')}', '${attName}')">Download</button>
                    </div>
                </div>
            `;
        });
        attachmentsHtml += '</div>';
    }
    return attachmentsHtml;
}

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

function renderThread(messages) {
    const container = document.getElementById('emailChainContainer');
    if (!messages || messages.length === 0) {
        container.innerHTML = '<p>No conversation history found.</p>';
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
        const messageCard = document.createElement('div');
        messageCard.className = `message-card ${msg.is_internal ? 'internal-note' : 'customer-message'}`;
        if (index === 0) messageCard.classList.add('expanded');

        const sender = escapeHtml(msg.sender || 'Unknown');
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

        messageCard.innerHTML = `
            <div class="message-header" style="flex-direction: column; align-items: flex-start; padding: 8px 12px;">
                <div style="display: flex; justify-content: space-between; width: 100%; align-items: center;">
                    <span class="message-sender">${sender} <span style="font-weight:normal; opacity:0.8; font-size:0.8rem;">(${msg.is_internal ? 'Internal Note' : 'Customer'})</span></span>
                    <span class="message-time">${timestamp}</span>
                </div>
                ${ccList ? `<div class="message-cc" style="font-size: 0.72rem; color: var(--text-secondary); margin-top: 4px; display: flex; gap: 4px; flex-wrap: wrap;"><strong>CC:</strong> <span style="opacity: 0.85;">${ccList}</span></div>` : ''}
                ${bccList ? `<div class="message-bcc" style="font-size: 0.72rem; color: var(--text-secondary); margin-top: 2px; display: flex; gap: 4px; flex-wrap: wrap;"><strong>BCC:</strong> <span style="opacity: 0.85;">${bccList}</span></div>` : ''}
            </div>
            <div class="message-body ${index === 0 ? 'visible' : ''}">
                <div class="email-iframe-container">
                    <iframe id="iframe-${msg.message_id || index}" sandbox="allow-same-origin allow-scripts allow-popups allow-popups-to-escape-sandbox" scrolling="no"></iframe>
                </div>
                
                ${sanitizedThread ? `
                <div class="thread-toggle-container" style="padding: 0 16px;">
                    <button class="quoted-text-btn" onclick="event.stopPropagation(); const container = this.nextElementSibling; const isHiding = container.style.display === 'none'; container.style.display = isHiding ? 'block' : 'none'; this.textContent = isHiding ? 'Hide Thread ▲' : 'Show Full Thread ▼'; if (isHiding) { const ifr = container.querySelector('iframe'); ifr.contentWindow.postMessage('trigger-resize', '*'); }">Show Full Thread ▼</button>
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
                        table { border-collapse: collapse; width: 100% !important; height: auto !important; }
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
        if (quillEditor) quillEditor.setText('');
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

async function saveTicket() {
    if (isTicketLocked) return;

    const emailContent = quillEditor.root.innerHTML;
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
            showToast('✓ Ticket saved successfully');
            loadTickets();
        } else {
            const errorMsg = data.error || 'Failed to save ticket';
            showToast(errorMsg, true);
        }
    } catch (error) {
        console.error('Error saving:', error);
        showToast('Network error while saving', true);
    }
}

async function regenerateAI() {
    if (isTicketLocked || !currentTicketIdStr) return;

    const userInstructions = prompt("Any specific instructions for the AI? (e.g. 'Make it more formal', 'Address the error code specifically', or leave blank for a standard regen)");

    if (userInstructions === null) return;

    const btn = document.getElementById('btnRegenerateAI');
    const originalHTML = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>Generating...';

    try {
        const currentDraft = quillEditor ? quillEditor.root.innerHTML : '';

        const response = await fetch('/api/regenerate_ai_sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ticket_id: currentTicketIdStr,
                user_instructions: userInstructions,
                current_draft: currentDraft
            })
        });

        const data = await response.json();

        if (response.ok && data.success) {
            showToast('✅ AI Draft regenerated successfully!', false);
            if (quillEditor && data.draft) {
                quillEditor.root.innerHTML = data.draft;
                quillEditor.setSelection(0, 0);
            }
        } else {
            showToast(data.error || 'Failed to regenerate AI draft', true);
        }
    } catch (error) {
        console.error('Error in synchronous regeneration:', error);
        showToast('Network error while regenerating AI draft', true);
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalHTML;
    }
}

async function sendToCustomer() {
    if (isTicketLocked) return;

    const emailContent = quillEditor.root.innerHTML;
    const assignedTo = document.getElementById('modal-assignment').value;
    const sendTo = document.getElementById('emailTo').value;

    if (!emailContent.trim() || emailContent === '<p><br></p>') {
        alert('Please add a response.');
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
    formData.append('cc', document.getElementById('emailCC').value);
    formData.append('bcc', document.getElementById('emailBCC').value);

    attachedFiles.forEach((file, index) => {
        formData.append(`attachment_${index}`, file);
    });
    formData.append('attachment_count', attachedFiles.length);

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

        alert('Email sent successfully! Ticket remains open.');
        loadTickets();
    } catch (error) {
        console.error('Error sending:', error);
        alert('Error sending email.');
    }
}

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

    await fetchContacts();
    setupAutocomplete('emailTo', 'suggestionsTo');
    setupAutocomplete('emailCC', 'suggestionsCC');
    setupAutocomplete('emailBCC', 'suggestionsBCC');

    await loadTickets();

    const pillContainer = document.getElementById('visibilityPill');
    if (pillContainer) {
        pillContainer.setAttribute('data-active', currentVisibility);
        document.querySelectorAll('.pill-option').forEach(o => {
            o.classList.toggle('active', o.dataset.visibility === currentVisibility);
        });
    }

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

// Auto-refresh
setInterval(() => {
    const indicator = document.getElementById('refreshIndicator');
    if (indicator) {
        indicator.classList.add('show');
        loadTickets();
        setTimeout(() => indicator.classList.remove('show'), 2000);
    }
}, 30000);

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

    const update = (id, val) => { const el = document.getElementById(id); if(el) el.textContent = val; };
    
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
        const btnToggle = document.getElementById('btnToggleStatus');
        if (btnSave) btnSave.disabled = true;
        if (btnSend) btnSend.disabled = true;
        if (btnToggle) btnToggle.disabled = true;
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
        const btnToggle = document.getElementById('btnToggleStatus');
        if (btnSave) btnSave.disabled = false;
        if (btnSend) btnSend.disabled = false;
        if (btnToggle) btnToggle.disabled = false;
        isTicketLocked = false;
        socket.emit('lock_ticket', { ticket_id: currentTicketIdStr });
    }
});