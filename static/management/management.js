const socket = io({ transports: ['websocket', 'polling'] });
let currentMetrics = { staff: {}, clients: {} };
let trendChart = null;
let distChart = null;

// Initialize WebSockets
socket.on('connect', () => {
    console.log('✅ Connected to Management Socket');
    refreshMetrics();
    refreshReassignmentList();
    refreshClientStats();
    socket.emit('get_settings');
});

// Handle metrics update
socket.on('staff_metrics_update', (data) => {
    data.metrics.forEach(m => {
        currentMetrics.staff[m.username] = m;
        const elAssigned = document.getElementById(`assigned-${m.username}`);
        const elOpen = document.getElementById(`open-${m.username}`);
        const elClosed = document.getElementById(`closed-${m.username}`);
        const elFrt = document.getElementById(`frt-${m.username}`);
        const elRes = document.getElementById(`res-${m.username}`);
        const elReopens = document.getElementById(`reopens-${m.username}`);
        
        if (elAssigned) elAssigned.textContent = m.assigned;
        if (elOpen) elOpen.textContent = m.open;
        if (elClosed) elClosed.textContent = m.closed;
        if (elFrt) elFrt.textContent = m.avg_frt + 'm';
        if (elRes) elRes.textContent = m.avg_res + 'h';
        if (elReopens) elReopens.textContent = m.reopens;
    });

    // Sort staff cards dynamically by total assigned tickets (maximum tickets on top)
    const grid = document.getElementById('staffMetricsGrid');
    if (grid) {
        const cards = Array.from(grid.querySelectorAll('.staff-metric-card'));
        cards.sort((a, b) => {
            const usernameA = a.id.replace('metric-', '');
            const usernameB = b.id.replace('metric-', '');
            const countA = (currentMetrics.staff[usernameA] && currentMetrics.staff[usernameA].assigned) || 0;
            const countB = (currentMetrics.staff[usernameB] && currentMetrics.staff[usernameB].assigned) || 0;
            return countB - countA; // Descending
        });
        cards.forEach(card => grid.appendChild(card));
    }
});

// Handle reassignment refresh
socket.on('ticket_reassigned', () => {
    refreshReassignmentList();
    refreshMetrics(); // Counts might change
});

socket.on('client_stats_update', (data) => {
    const container = document.getElementById('clientStatsGrid');
    if(!container) return;
    container.innerHTML = '';
    if (!data.stats || data.stats.length === 0) {
        container.innerHTML = '<div class="col-12 text-center py-5 text-secondary" style="grid-column: 1 / -1;">No client data found for this range.</div>';
        return;
    }
    // Sort client stats by total tickets in descending order
    const sortedStats = [...data.stats].sort((a, b) => b.total_tickets - a.total_tickets);
    sortedStats.forEach(s => {
        currentMetrics.clients[s.domain] = s;
        const card = document.createElement('div');
        card.className = 'staff-metric-card client-card';
        card.onclick = () => openDrilldown('client', s.domain);
        card.innerHTML = `
            <div class="staff-name text-truncate" title="${s.domain}">
                <span>🏢</span> <span class="client-domain-name">${s.domain}</span>
            </div>
            <div class="metric-stats">
                <div class="metric-item">
                    <span class="metric-value text-primary">${s.total_tickets}</span>
                    <span class="metric-label">Total</span>
                </div>
                <div class="metric-item">
                    <span class="metric-value text-success">${s.open_tickets}</span>
                    <span class="metric-label">Open</span>
                </div>
                <div class="metric-item">
                    <span class="metric-value text-secondary">${s.closed_tickets}</span>
                    <span class="metric-label">Closed</span>
                </div>
                <div class="metric-item">
                    <span class="metric-value text-info">${s.avg_frt}m</span>
                    <span class="metric-label">Avg FRT</span>
                </div>
                <div class="metric-item">
                    <span class="metric-value text-warning">${s.avg_res}h</span>
                    <span class="metric-label">Avg Res</span>
                </div>
            </div>
            <div class="mt-3 text-center small text-secondary border-top pt-2">
                Last Activity: ${s.last_activity ? s.last_activity.split('T')[0] : 'N/A'}
            </div>
        `;
        container.appendChild(card);
    });
    filterClientCards();
});

function filterClientCards() {
    const searchInput = document.getElementById('clientSearch');
    if(!searchInput) return;
    const query = searchInput.value.toLowerCase();
    const cards = document.querySelectorAll('.client-card');
    cards.forEach(card => {
        const domainName = card.querySelector('.client-domain-name').textContent.toLowerCase();
        card.style.display = domainName.includes(query) ? 'block' : 'none';
    });
}

socket.on('ticket_timeline_update', (data) => {
    const container = document.getElementById('systemLogs');
    if (!container) return;
    
    // If it's a general refresh, clear first
    container.innerHTML = '';
    
    if (!data.timeline || data.timeline.length === 0) {
        container.innerHTML = '<p class="text-center py-4">No events found.</p>';
        return;
    }

    data.timeline.forEach(ev => {
        const div = document.createElement('div');
        div.className = 'mb-3 pb-3 border-bottom';
        
        const timeStr = new Date(ev.timestamp).toLocaleString();
        let messageHtml = '';
        
        if (ev.event_type === 'assigned') {
            if (ev.details && ev.details.to) {
                messageHtml = `Assigned to <strong>${ev.details.to}</strong>`;
                if (ev.details.from && ev.details.from !== 'None') {
                    messageHtml += ` (previously ${ev.details.from})`;
                }
            } else {
                messageHtml = 'Assignment updated';
            }
        } else if (ev.event_type === 'created') {
            messageHtml = 'Ticket was created and added to the queue.';
        } else if (ev.event_type === 'status_change') {
            if (ev.details && ev.details.new_status) {
                messageHtml = `Status changed to <span style="color:var(--accent); font-weight:bold;">${ev.details.new_status}</span>`;
            } else {
                messageHtml = 'Ticket status updated';
            }
        } else if (ev.event_type === 'note' || ev.event_type === 'system_note') {
            messageHtml = ev.details && ev.details.note ? ev.details.note : 'Note added';
        } else {
            if (ev.details) {
                try {
                    messageHtml = `<pre style="margin:0; font-size:0.75rem; color: #a5b4fc; background: rgba(0,0,0,0.1); padding: 5px; border-radius: 4px;">${JSON.stringify(ev.details, null, 2)}</pre>`;
                } catch(e) { messageHtml = String(ev.details); }
            }
        }

        const isEmail = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(ev.actor);
        const actorLabel = isEmail ? 'Customer' : 'Actor';

        div.innerHTML = `
            <div class="d-flex justify-content-between align-items-center mb-1">
                <span class="badge" style="background: var(--accent); color: white;">${ev.event_type.toUpperCase()}</span>
                <span class="text-muted small">${timeStr}</span>
            </div>
            <div style="font-size: 0.9rem;">
                <div class="mb-1"><strong>Ticket:</strong> #${ev.display_id || (ev.ticket_id ? ev.ticket_id.substring(0, 8) + '...' : 'Unknown')}</div>
                <div class="mb-1">${messageHtml}</div>
                <div class="text-muted small"><strong>${actorLabel}:</strong> ${ev.actor || 'System'}</div>
            </div>
        `;
        container.appendChild(div);
    });
});

socket.on('settings_update', (data) => {
    const form = document.getElementById('settingsForm');
    for (const [key, value] of Object.entries(data.settings)) {
        const input = form.querySelector(`[name="${key}"]`);
        if (input) {
            if (input.type === 'checkbox') {
                input.checked = (value === 'True');
            } else {
                input.value = value;
            }
        }
    }
});

socket.on('settings_saved', (data) => {
    if (data.success) {
        alert('✅ Settings saved successfully!');
    } else {
        alert('❌ Failed to save settings' + (data.message ? ': ' + data.message : '.'));
    }
});

// Navigation Logic
function showSection(sectionId) {
    // Update nav tabs
    document.querySelectorAll('.nav-tab').forEach(tab => {
        tab.classList.remove('active');
        if (tab.textContent.toLowerCase().includes(sectionId)) {
            tab.classList.add('active');
        }
    });

    // Show section
    document.querySelectorAll('.management-section').forEach(sec => {
        sec.classList.remove('active');
    });
    const target = document.getElementById(`section-${sectionId}`);
    if (target) target.classList.add('active');

    // Trigger refreshes
    if (sectionId === 'client') refreshClientStats();
    if (sectionId === 'tracking') refreshSystemLogs();
    if (sectionId === 'settings') socket.emit('get_settings');
}

function refreshClientStats() {
    const range = document.getElementById('clientTimeRange').value;
    socket.emit('get_client_stats', { time_range: range });
}

function refreshSystemLogs() {
    socket.emit('get_ticket_timeline', { ticket_id: 'GLOBAL' }); 
}

function saveSettings() {
    const form = document.getElementById('settingsForm');
    const data = {};
    new FormData(form).forEach((value, key) => {
        data[key] = value;
    });
    // Handle switches explicitly since FormData omits unchecked ones
    form.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        data[cb.name] = cb.checked ? 'True' : 'False';
    });
    socket.emit('update_settings', data);
}

function filterReassignmentTable() {
    const query = document.getElementById('reassignSearch').value.toLowerCase();
    const rows = document.querySelectorAll('#reassignmentList tr');
    rows.forEach(row => {
        const text = row.innerText.toLowerCase();
        row.style.display = text.includes(query) ? '' : 'none';
    });
}

function filterTrackingLogs() {
    const query = document.getElementById('trackingSearch').value.toLowerCase();
    const events = document.querySelectorAll('#systemLogs > div');
    events.forEach(ev => {
        const text = ev.innerText.toLowerCase();
        ev.style.display = text.includes(query) ? '' : 'none';
    });
}

// Theme Toggle Logic
function toggleTheme() {
    const body = document.body;
    const isDark = body.classList.toggle('dark-mode');
    localStorage.setItem('darkMode', isDark);
    updateThemeButton(isDark);
}

function updateThemeButton(isDark) {
    const icon = document.getElementById('theme-icon');
    const text = document.getElementById('theme-text');
    if (icon) icon.textContent = isDark ? '☀️' : '🌙';
    if (text) text.textContent = isDark ? 'Light Mode' : 'Dark Mode';
}

// Initialize Theme
const savedTheme = localStorage.getItem('darkMode') === 'true';
if (savedTheme) {
    document.body.classList.add('dark-mode');
    updateThemeButton(true);
}

function refreshMetrics() {
    const range = document.getElementById('timeRangeSelect').value;
    socket.emit('get_staff_metrics', { time_range: range });
}


async function refreshReassignmentList() {
    try {
        const response = await fetch('/api/tickets');
        const data = await response.json();
        const tbody = document.getElementById('reassignmentList');
        tbody.innerHTML = '';

        // Show only open tickets for reassignment
        const openTickets = data.tickets.filter(t => t.status === 'Open');

        if (openTickets.length === 0) {
            tbody.innerHTML = '<tr><td colspan="2" class="text-center p-4">No open tickets to reassign.</td></tr>';
            return;
        }

        // Calculate active assignees (anyone who is active AND assignable, or currently assigned)
        const assignableUsers = allUsers.filter(u => u.is_active && u.is_assignable);

        openTickets.forEach(ticket => {
            const tr = document.createElement('tr');

            // Filter assignable users
            const assignableUsers = allUsers.filter(u => u.is_active && u.is_assignable);

            let options = `<option value="Unassigned" ${ticket.assigned_to === 'Unassigned' ? 'selected' : ''}>Unassigned</option>`;

            // Ensure current assignee is in the list even if opted out
            const dropdownUsers = [...assignableUsers];
            if (ticket.assigned_to !== 'Unassigned' && !dropdownUsers.find(u => u.username === ticket.assigned_to)) {
                dropdownUsers.push({ username: ticket.assigned_to });
            }

            dropdownUsers.forEach(u => {
                options += `<option value="${u.username}" ${ticket.assigned_to === u.username ? 'selected' : ''}>${u.username}</option>`;
            });

            tr.innerHTML = `
                <td class="p-3">
                    <div class="fw-bold text-truncate" style="max-width: 450px;" title="${ticket.subject}">${ticket.subject}</div>
                    <div class="small text-secondary">
                        <span class="badge bg-light text-dark border me-1">${ticket.display_id || ticket.ticket_id.substring(0, 8) + '...'}</span> 
                        <span class="text-muted">${ticket.customer_email}</span>
                    </div>
                </td>
                <td style="width: 200px;">
                    <select class="form-select form-select-sm" onchange="reassignTicket('${ticket.ticket_id}', this.value)">
                        ${options}
                    </select>
                </td>
            `;
            tbody.appendChild(tr);
        });

    } catch (err) {
        console.error('Failed to fetch tickets:', err);
    }
}

function reassignTicket(ticketId, username) {
    socket.emit('reassign_ticket', { ticket_id: ticketId, assigned_to: username });
}

function showChangePasswordModal(username) {
    document.getElementById('changePasswordUsername').textContent = username;
    document.getElementById('changePasswordForm').action = `/admin/users/${username}/change-password`;
    new bootstrap.Modal(document.getElementById('changePasswordModal')).show();
}

document.getElementById('timeRangeSelect').addEventListener('change', refreshMetrics);
document.getElementById('btnRefreshTickets').addEventListener('click', refreshReassignmentList);
document.getElementById('btnAutoAssign').addEventListener('click', () => {
    if (confirm("This will automatically assign open, unassigned tickets to active staff members using the load balancer. Continue?")) {
        socket.emit('trigger_auto_assign');
    }
});

// Auto-refresh every 30 seconds

function openDrilldown(type, id) {
    const data = type === 'staff' ? currentMetrics.staff[id] : currentMetrics.clients[id];
    if (!data) {
        console.warn(`No metrics found for ${type}: ${id}`);
        return;
    }

    // Update Header
    document.getElementById('drilldownTitle').textContent = id;
    document.getElementById('drilldownSubtitle').textContent = type === 'staff' ? 'Support Agent Performance' : 'Client Engagement Metrics';
    document.getElementById('drilldownIcon').textContent = type === 'staff' ? '👤' : '🏢';

    // Update KPIs with REAL data
    const total = type === 'staff' ? data.assigned : data.total_tickets;
    const closed = type === 'staff' ? data.closed : data.closed_tickets;
    const open = type === 'staff' ? data.open : data.open_tickets;

    document.getElementById('drill-kpi-total').textContent = total;
    document.getElementById('drill-kpi-frt').textContent = (data.avg_frt || 0) + 'm';
    document.getElementById('drill-kpi-res').textContent = (data.avg_res || 0) + 'h';
    
    const efficiency = (open === 0 && closed === 0) ? 0 : Math.round((closed / (open + closed)) * 100);
    document.getElementById('drill-kpi-efficiency').textContent = efficiency + '%';

    renderCharts(type, data);

    const modalEl = document.getElementById('drilldownModal');
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.show();
}

function renderCharts(type, data) {
    const ctxTrend = document.getElementById('drilldownChart').getContext('2d');
    const ctxDist = document.getElementById('distributionChart').getContext('2d');

    if (trendChart) trendChart.destroy();
    if (distChart) distChart.destroy();

    const openCount = type === 'staff' ? data.open : data.open_tickets;
    const closedCount = type === 'staff' ? data.closed : data.closed_tickets;
    const totalCount = type === 'staff' ? data.assigned : data.total_tickets;

    // Line Chart: Using summary data to create a "Recent Activity" view
    // Since we don't have daily breakdown, we show the scale of work
    const labels = ['Past 7 Days'];
    const trendData = [totalCount];

    trendChart = new Chart(ctxTrend, {
        type: 'bar', // Switched to bar for summary if only 1 data point, or line with dummy trend
        data: {
            labels: ['Total Tickets', 'Resolved Tickets', 'Active Tickets'],
            datasets: [{
                label: 'Volume',
                data: [totalCount, closedCount, openCount],
                backgroundColor: ['#4f46e5', '#10b981', '#f59e0b'],
                borderRadius: 8
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true } }
        }
    });

    distChart = new Chart(ctxDist, {
        type: 'doughnut',
        data: {
            labels: ['Open', 'Closed'],
            datasets: [{
                data: [openCount, closedCount],
                backgroundColor: ['#f59e0b', '#10b981'],
                borderWidth: 0,
                hoverOffset: 10
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { 
                legend: { position: 'bottom' },
                tooltip: {
                    callbacks: {
                        label: (item) => ` ${item.label}: ${item.raw} tickets`
                    }
                }
            },
            cutout: '70%'
        }
    });
}
