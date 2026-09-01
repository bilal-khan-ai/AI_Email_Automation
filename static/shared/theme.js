// Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Robust Global Theme Management - start
function toggleTheme() {
    const isDark = document.body ? document.body.classList.toggle('dark-mode') : document.documentElement.classList.toggle('dark-mode');
    if (document.body) {
        document.body.classList.toggle('dark-mode', isDark);
    }
    document.documentElement.classList.toggle('dark-mode', isDark);
    localStorage.setItem('darkMode', isDark);
    updateThemeButton(isDark);
}

function updateThemeButton(isDark) {
    const icon = document.getElementById('theme-icon');
    const text = document.getElementById('theme-text');
    const toggleBtn = document.querySelector('.theme-toggle');
    
    if (icon) {
        icon.className = isDark ? 'fa fa-sun' : 'fa fa-moon';
        icon.textContent = '';
    }
    if (text) {
        text.textContent = isDark ? 'Light Mode' : 'Dark Mode';
    }
    if (toggleBtn) {
        toggleBtn.setAttribute('title', isDark ? 'Switch to Light Mode' : 'Switch to Dark Mode');
        toggleBtn.setAttribute('aria-label', isDark ? 'Switch to Light Mode' : 'Switch to Dark Mode');
    }
}

// Immediately apply saved theme on load to prevent flash
(function() {
    const savedTheme = localStorage.getItem('darkMode') === 'true';
    if (savedTheme) {
        document.documentElement.classList.add('dark-mode');
    }
    
    function applyTheme() {
        if (savedTheme) {
            if (document.body) document.body.classList.add('dark-mode');
            document.documentElement.classList.add('dark-mode');
        } else {
            if (document.body) document.body.classList.remove('dark-mode');
            document.documentElement.classList.remove('dark-mode');
        }
        updateThemeButton(savedTheme);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', applyTheme);
    } else {
        applyTheme();
    }
})();
// Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Robust Global Theme Management - end
