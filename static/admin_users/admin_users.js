// Auto-dismiss alerts
setTimeout(function() {
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(function(alert) {
        const bsAlert = new bootstrap.Alert(alert);
        bsAlert.close();
    });
}, 5000);

// Change Password Modal
function showChangePasswordModal(username) {
    document.getElementById('changePasswordUsername').textContent = username;
    document.getElementById('changePasswordForm').action = 
        `/admin/users/${username}/change-password`;
    
    const modal = new bootstrap.Modal(document.getElementById('changePasswordModal'));
    modal.show();
}

// Form validation
document.getElementById('createUserForm').addEventListener('submit', function(e) {
    const username = this.querySelector('[name="username"]').value;
    const password = this.querySelector('[name="password"]').value;
    
    if (username.length < 3) {
        e.preventDefault();
        alert('Username must be at least 3 characters long');
        return;
    }
    
    if (password.length < 6) {
        e.preventDefault();
        alert('Password must be at least 6 characters long');
        return;
    }
});
