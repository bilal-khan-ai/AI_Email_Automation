const socket = io({ transports: ['websocket', 'polling'] });
let currentMetrics = { staff: {}, clients: {} };
let trendChart = null;
let distChart = null;
let activeDrilldown = { type: null, id: null };

// Initialize WebSockets
socket.on('connect', () => {
    console.log('✅ Connected to Management Socket');
    refreshMetrics();
    socket.emit('get_settings');
});

// Format Duration Utility: < 60 mins -> min, >= 60 mins -> hrs with decimals
function formatDuration(minutes) {
    if (minutes === null || minutes === undefined) return '0 min';
    const val = parseFloat(minutes);
    if (isNaN(val)) return '0 min';
    if (val < 60) return `${Math.round(val)} min`;
    return `${(val / 60).toFixed(1)} hrs`;
}

// Format Date Utility: dd-MM-yyyy
function formatDateDMY(isoString) {
    if (!isoString) return 'N/A';
    const date = new Date(isoString);
    if (isNaN(date.getTime())) return isoString;
    const day = String(date.getDate()).padStart(2, '0');
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const year = date.getFullYear();
    return `${day}-${month}-${year}`;
}

// Handle metrics update
socket.on('staff_metrics_update', (data) => {
    data.metrics.forEach(m => {
        currentMetrics.staff[m.username] = m;
        const elAssigned = document.getElementById(`assigned-${m.username}`);
        const elOpen = document.getElementById(`open-${m.username}`);
        const elClosed = document.getElementById(`closed-${m.username}`);
        const elFrt = document.getElementById(`frt-${m.username}`);
        const elRes = document.getElementById(`res-${m.username}`);
        const elEff = document.getElementById(`eff-${m.username}`);

        if (elAssigned) elAssigned.textContent = m.assigned;
        if (elOpen) elOpen.textContent = m.open;
        if (elClosed) elClosed.textContent = m.closed;
        if (elFrt) elFrt.textContent = formatDuration(m.avg_frt);
        if (elRes) elRes.textContent = formatDuration(m.avg_res * 60); // avg_res is in hours
        
        if (elEff) {
            const reopens = m.reopens || 0;
            const resolutionRate = (m.open === 0 && m.closed === 0) ? 0 : (m.closed / (m.open + m.closed));
            const reopenPenalty = m.closed === 0 ? 0 : Math.min(reopens / m.closed, 1);
            const efficiency = Math.round(resolutionRate * (1 - reopenPenalty) * 100);
            elEff.textContent = efficiency + '%';
        }

        const card = document.getElementById(`metric-${m.username}`);
        if (card) {
            if (m.assigned === 0) {
                card.style.display = 'none';
                card.classList.add('zero-assigned');
            } else {
                card.style.display = '';
                card.classList.remove('zero-assigned');
            }
        }
    });

    // Sort staff cards dynamically by total assigned tickets
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

socket.on('client_stats_update', (data) => {
    const container = document.getElementById('clientStatsGrid');
    if (!container) return;
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
                    <span class="metric-label">Close</span>
                </div>
                <div class="metric-item">
                    <span class="metric-value text-info">${formatDuration(s.avg_frt)}</span>
                    <span class="metric-label">FRT</span>
                </div>
                <div class="metric-item">
                    <span class="metric-value text-warning">${formatDuration(s.avg_res * 60)}</span>
                    <span class="metric-label">TTR</span>
                </div>
                <div class="metric-item">
                    <span class="metric-value text-success">${s.fcr}</span>
                    <span class="metric-label">FCR</span>
                </div>
            </div>
            <div class="mt-3 text-center small text-secondary border-top pt-2">
                Last Activity: ${formatDateDMY(s.last_activity)}
            </div>
        `;
        container.appendChild(card);
    });
    onReportSearch();
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
    if (sectionId === 'reports') refreshMetrics();
    if (sectionId === 'settings') socket.emit('get_settings');
    if (sectionId === 'holidays') loadHolidays();
    if (sectionId === 'management') loadClientGroups();
}

// ============================================================================
// Client Groups Management
// ============================================================================

function loadClientGroups() {
    fetch('/api/client_groups')
        .then(res => res.json())
        .then(data => {
            if (data.groups) {
                renderClientGroupsTable(data.groups);
                updateMergeDatalist(data.groups);
            }
        })
        .catch(err => console.error("Error loading client groups:", err));
}

function updateMergeDatalist(groups) {
    const dataList = document.getElementById('existingClientList');
    if (!dataList) return;
    dataList.innerHTML = '';
    
    // Only show existing groups in the datalist
    const existingGroups = groups.filter(g => g.type === 'group');
    existingGroups.forEach(g => {
        const option = document.createElement('option');
        option.value = g.name;
        dataList.appendChild(option);
    });
}

function renderClientGroupsTable(groups) {
    const tbody = document.getElementById('clientGroupsTableBody');
    if (!tbody) return;
    tbody.innerHTML = '';
    
    if (groups.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center py-4 text-secondary">No clients found.</td></tr>';
        return;
    }

    groups.forEach(g => {
        const tr = document.createElement('tr');
        tr.style.cursor = 'pointer';
        tr.onclick = function(e) {
            if (e.target.tagName !== 'INPUT' && e.target.tagName !== 'BUTTON') {
                const cb = this.querySelector('.client-checkbox');
                cb.checked = !cb.checked;
                updateMergeUI();
            }
        };
        
        const badge = g.type === 'group' ? '<span class="badge bg-primary">Group</span>' : '<span class="badge bg-secondary">Domain</span>';
        const actions = g.type === 'group' ? `<button class="btn btn-sm btn-outline-danger" onclick="deleteClientGroup(${g.id}, '${g.name}'); event.stopPropagation();">🗑️ Delete</button>` : '';

        tr.innerHTML = `
            <td><input type="checkbox" class="form-check-input client-checkbox" value="${g.name}" data-domains="${g.domains}" onclick="updateMergeUI(); event.stopPropagation();"></td>
            <td><strong>${g.name}</strong></td>
            <td style="word-wrap: break-word; white-space: normal; max-width: 300px;">${g.domains}</td>
            <td>${badge}</td>
            <td>${actions}</td>
        `;
        tbody.appendChild(tr);
    });
    updateMergeUI();
}

function toggleAllClientRows(source) {
    const checkboxes = document.querySelectorAll('.client-checkbox');
    checkboxes.forEach(cb => cb.checked = source.checked);
    updateMergeUI();
}

function updateMergeUI() {
    const checkboxes = document.querySelectorAll('.client-checkbox:checked');
    const container = document.getElementById('mergeActionContainer');
    if (checkboxes.length > 1) {
        container.classList.remove('d-none');
    } else {
        container.classList.add('d-none');
    }
}

function mergeSelectedClients() {
    const checkboxes = document.querySelectorAll('.client-checkbox:checked');
    if (checkboxes.length < 2) return;
    
    const inputName = document.getElementById('mergeClientNameInput').value.trim();
    if (!inputName) {
        alert("Please enter or select a Client Name for the merged group.");
        return;
    }
    
    let allDomains = [];
    checkboxes.forEach(cb => {
        const d = cb.getAttribute('data-domains');
        if (d) {
            d.split(',').forEach(domain => allDomains.push(domain.trim()));
        }
    });
    
    // Remove duplicates
    allDomains = [...new Set(allDomains)];
    
    fetch('/api/client_groups/merge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: inputName, domains: allDomains })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            document.getElementById('mergeClientNameInput').value = '';
            document.getElementById('selectAllClients').checked = false;
            loadClientGroups();
            refreshMetrics();
        } else {
            alert("Error merging clients: " + (data.error || "Unknown error"));
        }
    })
    .catch(err => {
        console.error("Error merging clients:", err);
        alert("Failed to merge clients.");
    });
}

function submitCreateClient(event) {
    event.preventDefault();
    const name = document.getElementById('newClientName').value;
    const domains = document.getElementById('newClientDomains').value;
    
    fetch('/api/client_groups/merge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, domains: domains.split(',').map(d => d.trim()) })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            document.getElementById('createClientForm').reset();
            loadClientGroups();
            refreshMetrics();
        } else {
            alert("Error: " + (data.error || "Failed to add client group"));
        }
    })
    .catch(err => {
        console.error("Error adding client group:", err);
        alert("Failed to add client group");
    });
}

function deleteClientGroup(id, name) {
    if (!confirm(`Are you sure you want to delete client group '${name}'?`)) return;

    fetch(`/api/client_groups/${id}`, {
        method: 'DELETE'
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            loadClientGroups();
            refreshMetrics();
        } else {
            alert("Error: " + (data.error || "Failed to delete client group"));
        }
    })
    .catch(err => {
        console.error("Error deleting client group:", err);
        alert("Failed to delete client group");
    });
}

// ============================================================================
// Exchange Holidays Management
// ============================================================================

let selectedHolidayDates = [];
let allHolidays = [];

// Load holidays from DB
function loadHolidays() {
    fetch('/api/holidays')
        .then(res => res.json())
        .then(data => {
            if (data.holidays) {
                allHolidays = data.holidays;
                selectedHolidayDates = []; // Reset selection on reload
                updateHolidayButtonsVisibility();
                renderHolidaysTable(allHolidays);
            }
        })
        .catch(err => console.error("Error loading holidays:", err));
}

// Render holidays table
function renderHolidaysTable(holidays) {
    const tbody = document.getElementById('holidaysTableBody');
    const badge = document.getElementById('holidaysCountBadge');
    if (!tbody) return;
    tbody.innerHTML = '';
    
    if (badge) {
        badge.textContent = `${holidays.length} Total`;
    }

    if (holidays.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center py-4 text-secondary">No holidays defined. Click Fetch or Add to begin.</td></tr>';
        return;
    }

    holidays.forEach((h, index) => {
        const tr = document.createElement('tr');
        const isSelected = selectedHolidayDates.includes(h.date);
        if (isSelected) {
            tr.classList.add('table-primary');
        }
        
        tr.onclick = (e) => {
            const date = h.date;
            const idx = selectedHolidayDates.indexOf(date);
            if (idx > -1) {
                selectedHolidayDates.splice(idx, 1);
                tr.classList.remove('table-primary');
            } else {
                selectedHolidayDates.push(date);
                tr.classList.add('table-primary');
            }
            updateHolidayButtonsVisibility();
        };

        tr.innerHTML = `
            <td>${index + 1}</td>
            <td style="word-wrap: break-word; white-space: normal; max-width: 300px;">${h.holiday}</td>
            <td>${formatDateDMY(h.date)}</td>
            <td>${h.day}</td>
        `;
        tbody.appendChild(tr);
    });
}

// Update buttons visibility based on selected rows
function updateHolidayButtonsVisibility() {
    const editBtn = document.getElementById('editHolidayBtn');
    const deleteBtn = document.getElementById('deleteHolidayBtn');
    
    if (selectedHolidayDates.length === 0) {
        if (editBtn) editBtn.style.display = 'none';
        if (deleteBtn) deleteBtn.style.display = 'none';
    } else if (selectedHolidayDates.length === 1) {
        if (editBtn) editBtn.style.display = 'inline-block';
        if (deleteBtn) deleteBtn.style.display = 'inline-block';
    } else {
        if (editBtn) editBtn.style.display = 'none';
        if (deleteBtn) deleteBtn.style.display = 'inline-block';
    }
}

// Derive day name from date input
function deriveDayName(prefix) {
    const dateInput = document.getElementById(`${prefix}HolidayDate`);
    const dayInput = document.getElementById(`${prefix}HolidayDay`);
    if (!dateInput || !dayInput) return;
    
    const val = dateInput.value;
    if (!val) {
        dayInput.value = '';
        return;
    }
    
    const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
    const d = new Date(val);
    if (!isNaN(d.getTime())) {
        dayInput.value = days[d.getUTCDay()];
    } else {
        dayInput.value = '';
    }
}

// Add holiday
function showAddHolidayModal() {
    document.getElementById('addHolidayForm').reset();
    document.getElementById('addHolidayDay').value = '';
    const modal = new bootstrap.Modal(document.getElementById('addHolidayModal'));
    modal.show();
}

function submitAddHoliday(event) {
    event.preventDefault();
    const holiday = document.getElementById('addHolidayName').value;
    const date = document.getElementById('addHolidayDate').value;
    
    fetch('/api/holidays', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ holiday, date })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            const modalEl = document.getElementById('addHolidayModal');
            bootstrap.Modal.getInstance(modalEl).hide();
            loadHolidays();
        } else {
            alert("Error: " + (data.error || "Failed to add holiday"));
        }
    })
    .catch(err => {
        console.error("Error adding holiday:", err);
        alert("Failed to add holiday");
    });
}

// Edit holiday
function showEditHolidayModal() {
    if (selectedHolidayDates.length !== 1) return;
    const dateStr = selectedHolidayDates[0];
    const holiday = allHolidays.find(h => h.date === dateStr);
    if (!holiday) return;

    document.getElementById('editHolidayOldDate').value = holiday.date;
    document.getElementById('editHolidayName').value = holiday.holiday;
    document.getElementById('editHolidayDate').value = holiday.date;
    deriveDayName('edit');

    const modal = new bootstrap.Modal(document.getElementById('editHolidayModal'));
    modal.show();
}

function submitEditHoliday(event) {
    event.preventDefault();
    const old_date = document.getElementById('editHolidayOldDate').value;
    const holiday = document.getElementById('editHolidayName').value;
    const date = document.getElementById('editHolidayDate').value;
    
    fetch('/api/holidays', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ old_date, holiday, date })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            const modalEl = document.getElementById('editHolidayModal');
            bootstrap.Modal.getInstance(modalEl).hide();
            loadHolidays();
        } else {
            alert("Error: " + (data.error || "Failed to update holiday"));
        }
    })
    .catch(err => {
        console.error("Error updating holiday:", err);
        alert("Failed to update holiday");
    });
}

// Delete selected holidays
function deleteSelectedHolidays() {
    if (selectedHolidayDates.length === 0) return;
    const msg = selectedHolidayDates.length === 1 
        ? "Are you sure you want to delete the selected holiday?" 
        : `Are you sure you want to delete the ${selectedHolidayDates.length} selected holidays?`;
        
    if (!confirm(msg)) return;

    fetch('/api/holidays', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dates: selectedHolidayDates })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            loadHolidays();
        } else {
            alert("Error: " + (data.error || "Failed to delete holidays"));
        }
    })
    .catch(err => {
        console.error("Error deleting holidays:", err);
        alert("Failed to delete holidays");
    });
}

// Import/Paste holidays
function showImportHolidayModal() {
    document.getElementById('importHolidayForm').reset();
    const modal = new bootstrap.Modal(document.getElementById('importHolidayModal'));
    modal.show();
}

function submitImportHolidays(event) {
    event.preventDefault();
    const text = document.getElementById('importHolidayText').value;
    const submitBtn = document.querySelector('#importHolidayForm button[type="submit"]');
    const originalText = submitBtn.innerHTML;
    submitBtn.disabled = true;
    submitBtn.innerHTML = '⌛ Parsing & Updating...';

    fetch('/api/holidays/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text })
    })
    .then(res => res.json())
    .then(data => {
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalText;
        if (data.success) {
            bootstrap.Modal.getInstance(document.getElementById('importHolidayModal')).hide();
            
            // Show summary modal
            document.getElementById('summaryAdded').textContent = data.summary.added;
            document.getElementById('summaryUpdated').textContent = data.summary.updated;
            document.getElementById('summaryMerged').textContent = data.summary.merged;
            document.getElementById('summarySkipped').textContent = data.summary.skipped;
            
            const summaryModal = new bootstrap.Modal(document.getElementById('importSummaryModal'));
            summaryModal.show();
            
            loadHolidays();
        } else {
            alert("Error: " + (data.error || "Failed to parse holidays"));
        }
    })
    .catch(err => {
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalText;
        console.error("Error importing holidays:", err);
        alert("Failed to parse holidays");
    });
}

// Search holidays client-side
function onHolidaySearch() {
    const query = document.getElementById('holidaySearchInput').value.toLowerCase();
    const filtered = allHolidays.filter(h => 
        h.holiday.toLowerCase().includes(query) || 
        h.date.toLowerCase().includes(query) || 
        h.day.toLowerCase().includes(query)
    );
    renderHolidaysTable(filtered);
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
    const fromDate = document.getElementById('fromDateInput').value;
    const toDate = document.getElementById('toDateInput').value;
    const payload = {
        time_range: range,
        from_date: range === 'custom' ? fromDate : null,
        to_date: range === 'custom' ? toDate : null
    };
    socket.emit('get_staff_metrics', payload);
    socket.emit('get_client_stats', payload);
}

function toggleReportView(view) {
    if (view === 'staff') {
        document.getElementById('staffViewPanel').classList.remove('d-none');
        document.getElementById('clientViewPanel').classList.add('d-none');
    } else {
        document.getElementById('staffViewPanel').classList.add('d-none');
        document.getElementById('clientViewPanel').classList.remove('d-none');
    }
    onReportSearch();
}

function toggleManagementView(view) {
    if (view === 'staff') {
        document.getElementById('managementStaffView').classList.remove('d-none');
        document.getElementById('managementClientView').classList.add('d-none');
    } else {
        document.getElementById('managementStaffView').classList.add('d-none');
        document.getElementById('managementClientView').classList.remove('d-none');
    }
}

function onTimeRangeChange() {
    const range = document.getElementById('timeRangeSelect').value;
    const customInputs = document.getElementById('customDateInputs');
    if (range === 'custom') {
        customInputs.classList.remove('d-none');
        customInputs.classList.add('d-flex');
    } else {
        customInputs.classList.remove('d-flex');
        customInputs.classList.add('d-none');
        refreshMetrics();
    }
}

function applyCustomDates() {
    refreshMetrics();
}

function onReportSearch() {
    const query = document.getElementById('reportSearch').value.toLowerCase();
    const isStaff = document.getElementById('reportViewPill').getAttribute('data-active') === 'staff';
    if (isStaff) {
        const cards = document.querySelectorAll('#staffMetricsGrid .staff-metric-card');
        cards.forEach(card => {
            const name = card.querySelector('.staff-name').textContent.toLowerCase();
            const isZero = card.classList.contains('zero-assigned');
            card.style.display = (name.includes(query) && !isZero) ? 'block' : 'none';
        });
    } else {
        const cards = document.querySelectorAll('.client-card');
        cards.forEach(card => {
            const domainName = card.querySelector('.client-domain-name').textContent.toLowerCase();
            card.style.display = domainName.includes(query) ? 'block' : 'none';
        });
    }
}

function showChangePasswordModal(username) {
    document.getElementById('changePasswordUsername').textContent = username;
    document.getElementById('changePasswordForm').action = `/admin/users/${username}/change-password`;
    new bootstrap.Modal(document.getElementById('changePasswordModal')).show();
}

function openDrilldown(type, id) {
    const data = type === 'staff' ? currentMetrics.staff[id] : currentMetrics.clients[id];
    if (!data) {
        console.warn(`No metrics found for ${type}: ${id}`);
        return;
    }

    activeDrilldown.type = type;
    activeDrilldown.id = id;
    activeDrilldown.status = null; // Clear filter initially

    const fcrWrap = document.getElementById('kpi-fcr-wrapper');
    const ttrWrap = document.getElementById('kpi-ttr-wrapper');
    const effWrap = document.getElementById('kpi-efficiency-wrapper');
    if (fcrWrap) fcrWrap.style.display = 'block';
    if (ttrWrap) ttrWrap.style.display = 'block';
    if (effWrap) effWrap.style.display = 'block';

    // Update Header
    document.getElementById('drilldownTitle').textContent = id;
    document.getElementById('drilldownSubtitle').textContent = type === 'staff' ? 'Support Agent Performance' : 'Client Engagement Metrics';
    document.getElementById('drilldownIcon').textContent = type === 'staff' ? '👤' : '🏢';

    // Update dynamic counter
    const total = type === 'staff' ? data.assigned : data.total_tickets;
    const elTrendCount = document.getElementById('trendTotalCount');
    if (elTrendCount) elTrendCount.textContent = total;
    const elStatusPrefix = document.getElementById('trendStatusPrefix');
    if (elStatusPrefix) elStatusPrefix.textContent = '';

    // Update KPIs with REAL data
    const closed = type === 'staff' ? data.closed : data.closed_tickets;
    const open = type === 'staff' ? data.open : data.open_tickets;

    document.getElementById('drill-kpi-frt').textContent = formatDuration(data.avg_frt);
    document.getElementById('drill-kpi-res').textContent = formatDuration(data.avg_res * 60); // avg_res is in hours

    const reopens = data.reopens || 0;
    const resolutionRate = (open === 0 && closed === 0) ? 0 : (closed / (open + closed));
    const reopenPenalty = closed === 0 ? 0 : Math.min(reopens / closed, 1);
    const efficiency = Math.round(resolutionRate * (1 - reopenPenalty) * 100);
    document.getElementById('drill-kpi-efficiency').textContent = efficiency + '%';

    // New metrics
    document.getElementById('drill-kpi-client-resp').textContent = formatDuration(data.avg_client_resp);
    document.getElementById('drill-kpi-iterations').textContent = data.avg_iterations !== undefined ? data.avg_iterations : 0;
    document.getElementById('drill-kpi-fcr').textContent = data.fcr !== undefined ? data.fcr : 0;

    renderCharts(type, data);

    const modalEl = document.getElementById('drilldownModal');
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.show();
}

// Socket listener for dynamic drilldown KPI updates
socket.on('drilldown_metrics_update', (data) => {
    if (activeDrilldown.type !== data.type || activeDrilldown.id !== data.id) return;
    
    // Update KPI visibility
    const fcrWrap = document.getElementById('kpi-fcr-wrapper');
    const ttrWrap = document.getElementById('kpi-ttr-wrapper');
    const effWrap = document.getElementById('kpi-efficiency-wrapper');
    
    if (activeDrilldown.status === 'reopen' || activeDrilldown.status === 'Reopen') {
        if (fcrWrap) fcrWrap.style.display = 'none';
        if (ttrWrap) ttrWrap.style.display = 'block';
        if (effWrap) effWrap.style.display = 'none';
    } else if (activeDrilldown.status === 'open' || activeDrilldown.status === 'Open') {
        if (fcrWrap) fcrWrap.style.display = 'none';
        if (ttrWrap) ttrWrap.style.display = 'none';
        if (effWrap) effWrap.style.display = 'none';
    } else {
        if (fcrWrap) fcrWrap.style.display = 'block';
        if (ttrWrap) ttrWrap.style.display = 'block';
        if (effWrap) effWrap.style.display = 'block';
    }

    // Update dynamic counter in header
    const total = data.type === 'staff' ? data.metrics.assigned : data.metrics.total_tickets;
    const elTrendCount = document.getElementById('trendTotalCount');
    if (elTrendCount) elTrendCount.textContent = total;
    const elStatusPrefix = document.getElementById('trendStatusPrefix');
    if (elStatusPrefix) {
        if (activeDrilldown.status) {
            elStatusPrefix.textContent = activeDrilldown.status.charAt(0).toUpperCase() + activeDrilldown.status.slice(1) + ' ';
        } else {
            elStatusPrefix.textContent = '';
        }
    }
    
    // Update KPIs
    document.getElementById('drill-kpi-frt').textContent = formatDuration(data.metrics.avg_frt);
    document.getElementById('drill-kpi-res').textContent = formatDuration(data.metrics.avg_res * 60);
    
    const open = data.type === 'staff' ? data.metrics.open : data.metrics.open_tickets;
    const closed = data.type === 'staff' ? data.metrics.closed : data.metrics.closed_tickets;
    const reopens = data.metrics.reopens || 0;
    const resolutionRate = (open === 0 && closed === 0) ? 0 : (closed / (open + closed));
    const reopenPenalty = closed === 0 ? 0 : Math.min(reopens / closed, 1);
    const efficiency = Math.round(resolutionRate * (1 - reopenPenalty) * 100);
    document.getElementById('drill-kpi-efficiency').textContent = efficiency + '%';
    
    document.getElementById('drill-kpi-client-resp').textContent = formatDuration(data.metrics.avg_client_resp);
    document.getElementById('drill-kpi-iterations').textContent = data.metrics.avg_iterations !== undefined ? data.metrics.avg_iterations : 0;
    document.getElementById('drill-kpi-fcr').textContent = data.metrics.fcr !== undefined ? data.metrics.fcr : 0;
});

function redirectToDashboard(status) {
    const timeRange = document.getElementById('timeRangeSelect').value;
    const fromDate = document.getElementById('fromDateInput').value;
    const toDate = document.getElementById('toDateInput').value;

    let url = `/?view=staff&status=${status}`;

    if (activeDrilldown.type === 'staff') {
        url += `&assignee=${encodeURIComponent(activeDrilldown.id)}`;
    } else if (activeDrilldown.type === 'client') {
        url += `&domain=${encodeURIComponent(activeDrilldown.id)}`;
    }

    if (timeRange === 'custom' && fromDate && toDate) {
        url += `&from=${fromDate}&to=${toDate}`;
    } else {
        url += `&time_range=${timeRange}`;
    }

    window.location.href = url;
}

function renderCharts(type, data) {
    const ctxTrend = document.getElementById('drilldownChart').getContext('2d');
    const ctxDist = document.getElementById('distributionChart').getContext('2d');

    if (trendChart) trendChart.destroy();
    if (distChart) distChart.destroy();

    const openCount = type === 'staff' ? data.open : data.open_tickets;
    const closedCount = type === 'staff' ? data.closed : data.closed_tickets;
    const reopenCount = data.reopens || 0;
    const ignoreCount = type === 'staff' ? (data.ignore || 0) : (data.ignore_tickets || 0);

    // Custom plugin to draw labels inside the bars touching the top
    const barLabelsPlugin = {
        id: 'barLabels',
        afterDatasetsDraw(chart) {
            const { ctx } = chart;
            ctx.save();
            ctx.font = 'bold 12px sans-serif';
            ctx.fillStyle = '#ffffff';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';
            chart.data.datasets.forEach((dataset, i) => {
                const meta = chart.getDatasetMeta(i);
                meta.data.forEach((bar, index) => {
                    const value = dataset.data[index];
                    if (value !== null && value !== undefined) {
                        ctx.fillText(value, bar.x, bar.y + 5);
                    }
                });
            });
            ctx.restore();
        }
    };

    trendChart = new Chart(ctxTrend, {
        type: 'bar',
        plugins: [barLabelsPlugin],
        data: {
            labels: ['Open', 'Close', 'Reopen', 'Ignore'],
            datasets: [{
                label: 'Volume',
                data: [openCount, closedCount, reopenCount, ignoreCount],
                backgroundColor: ['#f59e0b', '#10b981', '#ef4444', '#6b7280'],
                borderRadius: 8
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true } },
            onHover: (event, chartElement) => {
                event.native.target.style.cursor = chartElement[0] ? 'pointer' : 'default';
            },
            onClick: (event, elements) => {
                if (elements.length > 0) {
                    const index = elements[0].index;
                    const statuses = ['Open', 'Close', 'Reopen', 'Ignore'];
                    const clickedStatus = statuses[index];
                    
                    let mappedStatus = clickedStatus;
                    if (clickedStatus === 'Close') mappedStatus = 'closed';
                    if (clickedStatus === 'Reopen') mappedStatus = 'reopen';
                    
                    if (activeDrilldown.status === mappedStatus) {
                        activeDrilldown.status = null;
                    } else {
                        activeDrilldown.status = mappedStatus;
                    }
                    
                    // Request status-filtered KPIs
                    const range = document.getElementById('timeRangeSelect').value;
                    const fromDate = document.getElementById('fromDateInput').value;
                    const toDate = document.getElementById('toDateInput').value;
                    socket.emit('get_drilldown_metrics', {
                        type: activeDrilldown.type,
                        id: activeDrilldown.id,
                        status: activeDrilldown.status,
                        time_range: range,
                        from_date: range === 'custom' ? fromDate : null,
                        to_date: range === 'custom' ? toDate : null
                    });
                }
            }
        }
    });

    distChart = new Chart(ctxDist, {
        type: 'doughnut',
        data: {
            labels: ['Open', 'Close', 'Reopen', 'Ignore'],
            datasets: [{
                data: [openCount, closedCount, reopenCount, ignoreCount],
                backgroundColor: ['#f59e0b', '#10b981', '#ef4444', '#6b7280'],
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
            cutout: '70%',
            onHover: (event, chartElement) => {
                event.native.target.style.cursor = chartElement[0] ? 'pointer' : 'default';
            },
            onClick: (event, elements) => {
                if (elements.length > 0) {
                    const index = elements[0].index;
                    const statuses = ['Open', 'Close', 'Reopen', 'Ignore'];
                    const clickedStatus = statuses[index];
                    
                    let mappedStatus = clickedStatus;
                    if (clickedStatus === 'Close') mappedStatus = 'closed';
                    if (clickedStatus === 'Reopen') mappedStatus = 'reopen';
                    
                    if (activeDrilldown.status === mappedStatus) {
                        activeDrilldown.status = null;
                    } else {
                        activeDrilldown.status = mappedStatus;
                    }
                    
                    // Request status-filtered KPIs
                    const range = document.getElementById('timeRangeSelect').value;
                    const fromDate = document.getElementById('fromDateInput').value;
                    const toDate = document.getElementById('toDateInput').value;
                    socket.emit('get_drilldown_metrics', {
                        type: activeDrilldown.type,
                        id: activeDrilldown.id,
                        status: activeDrilldown.status,
                        time_range: range,
                        from_date: range === 'custom' ? fromDate : null,
                        to_date: range === 'custom' ? toDate : null
                    });
                }
            }
        }
    });
}

// Initialize Flatpickr date pickers and Bootstrap Popovers
document.addEventListener('DOMContentLoaded', () => {
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

    // Initialize Bootstrap Popovers (click to open, dismiss on focus out)
    if (typeof bootstrap !== 'undefined') {
        const popoverTriggerList = document.querySelectorAll('[data-bs-toggle="popover"]');
        [...popoverTriggerList].map(popoverTriggerEl => new bootstrap.Popover(popoverTriggerEl));
    }

    // Initialize report view to client by default
    toggleReportView('client');

    // Add Pill Slider Toggle Listeners
    const pillOptions = document.querySelectorAll('#reportViewPill .pill-option');
    pillOptions.forEach(opt => {
        opt.addEventListener('click', function() {
            const container = document.getElementById('reportViewPill');
            const view = this.dataset.view;
            document.querySelectorAll('#reportViewPill .pill-option').forEach(o => o.classList.remove('active'));
            this.classList.add('active');
            container.setAttribute('data-active', view);
            toggleReportView(view);
        });
    });

    const mgmtPillOptions = document.querySelectorAll('#managementViewPill .pill-option');
    if (mgmtPillOptions.length > 0) {
        mgmtPillOptions.forEach(opt => {
            opt.addEventListener('click', function() {
                const container = document.getElementById('managementViewPill');
                const view = this.dataset.view;
                document.querySelectorAll('#managementViewPill .pill-option').forEach(o => o.classList.remove('active'));
                this.classList.add('active');
                container.setAttribute('data-active', view);
                toggleManagementView(view);
            });
        });
    }
});
