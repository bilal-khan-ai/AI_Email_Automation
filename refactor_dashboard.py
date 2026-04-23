import os
import re

def refactor_dashboard():
    src = "templates/dashboard.html"
    if not os.path.exists(src):
        print(f"Error: {src} not found")
        return

    with open(src, "r", encoding="utf-8") as f:
        content = f.read()

    # Extract CSS
    css_match = re.search(r'<style>(.*?)</style>', content, re.DOTALL)
    if css_match:
        css_content = css_match.group(1)
    else:
        css_content = ""

    # Extract JS
    # We have multiple script tags. Let's extract the main one.
    # The last one is the main logic.
    js_match = re.search(r'<script>\s*let currentTickets = \[\];(.*?)</script>\s*</body>', content, re.DOTALL)
    if js_match:
        js_content = "let currentTickets = [];\n" + js_match.group(1)
    else:
        js_content = ""

    # Generate HTML
    html_content = re.sub(r'<style>.*?</style>', '<link rel="stylesheet" href="{{ url_for(\'static\', filename=\'dashboard.css\') }}">', content, flags=re.DOTALL)
    html_content = re.sub(r'<script>\s*let currentTickets = \[\];.*?</script>\s*</body>', '<script src="{{ url_for(\'static\', filename=\'dashboard.js\') }}"></script>\n</body>', html_content, flags=re.DOTALL)

    # ---------------- Clean up CSS ----------------
    # Remove unused status classes
    css_content = re.sub(r'\.status-pending-review,[\s\S]*?color: #155724;\s*}', '', css_content)
    css_content = re.sub(r'\.status-completed,[\s\S]*?color: #383d41;\s*}', '', css_content)
    
    # Remove unused SLA classes
    css_content = re.sub(r'/\* --- SLA Visual Indicators --- \*/[\s\S]*?body\.dark-mode \.sla-card-long-running \{[\s\S]*?\}', '', css_content)

    # Add new classes for inline styles
    new_css = """
/* Extracted inline styles */
.header-container {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    width: 100%;
}
.header-user-info {
    margin-top: 1rem;
    font-weight: 500;
    font-size: 0.9rem;
    opacity: 0.9;
}
.header-actions {
    display: flex;
    gap: 0.75rem;
    align-items: center;
}
.btn-header {
    color: white;
    border: 1px solid rgba(255,255,255,0.4);
    border-radius: 20px;
    padding: 0.4rem 1.2rem;
    text-decoration: none;
    font-size: 0.8rem;
    background: rgba(255,255,255,0.1);
}
.btn-logout {
    background: rgba(231, 76, 60, 0.8);
    border-radius: 20px;
    padding: 0.4rem 1.2rem;
    text-decoration: none;
    font-size: 0.8rem;
    color: white;
}
.password-modal-content {
    max-width: 400px;
    height: auto;
    align-self: center;
    display: block;
    position: relative;
}
.password-input {
    width: 100%;
    padding: 0.75rem;
    border: 2px solid var(--border-color);
    border-radius: 8px;
    background: var(--input-bg);
    color: var(--text-primary);
}
"""
    css_content += new_css

    # ---------------- Clean up JS ----------------
    # Remove unused functions
    js_content = re.sub(r'// --- Helper: Calculate Working Hours.*?return hours;\s*}', '', js_content, flags=re.DOTALL)
    js_content = re.sub(r'// --- Helper: Check if ticket has unanswered customer message.*?return workingHours > 24;\s*}', '', js_content, flags=re.DOTALL)
    js_content = re.sub(r'// --- Helper: Check if ticket is a long-running open ticket.*?return workingHours > 48;\s*}', '', js_content, flags=re.DOTALL)
    js_content = re.sub(r'function cleanMessageBody\(text\) \{[\s\S]*?return div\.innerHTML;\s*\}', '', js_content)

    # Move initIframe out of renderThread loop to avoid recreation
    init_iframe_code = r"""
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
                                    const rgb = bg.match(/\d+/g);
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
                                    const rgb = color.match(/\d+/g);
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
                <\/body>
                <\/html>
            `);
            doc.close();
        };
"""
    # Replace the inline initIframe in JS with a call to the global one
    # Note: Regex replacing such a big block is tricky. Let's just find and remove it, then prepend it.
    js_content = re.sub(r'const initIframe = \(iframeId, content\) => \{[\s\S]*?doc\.close\(\);\s*\};', '', js_content)
    js_content = init_iframe_code + "\n" + js_content

    # Fix escape sequences that might have been mangled during re.sub
    js_content = js_content.replace("\\<\\/script\\>", "</script>")
    js_content = js_content.replace("\\<\\/body\\>", "</body>")
    js_content = js_content.replace("\\<\\/html\\>", "</html>")

    # ---------------- Clean up HTML ----------------
    # Replace inline styles with new classes
    html_content = html_content.replace('style="display: flex; justify-content: space-between; align-items: flex-start; width: 100%;"', 'class="header-container"')
    html_content = html_content.replace('style="margin-top: 1rem; font-weight: 500; font-size: 0.9rem; opacity: 0.9;"', 'class="header-user-info"')
    html_content = html_content.replace('style="display: flex; gap: 0.75rem; align-items: center;"', 'class="header-actions"')
    
    btn_style = 'style="color: white; border: 1px solid rgba(255,255,255,0.4); border-radius: 20px; padding: 0.4rem 1.2rem; text-decoration: none; font-size: 0.8rem; background: rgba(255,255,255,0.1);"'
    html_content = html_content.replace(btn_style, 'class="btn btn-outline-light btn-header"')
    
    logout_style = 'style="background: rgba(231, 76, 60, 0.8); border-radius: 20px; padding: 0.4rem 1.2rem; text-decoration: none; font-size: 0.8rem;"'
    html_content = html_content.replace(logout_style, 'class="btn btn-danger btn-sm btn-logout"')

    modal_content_style = 'style="max-width: 400px; height: auto; align-self: center; display: block; position: relative;"'
    html_content = html_content.replace(modal_content_style, 'class="modal-content password-modal-content"')
    
    pwd_input_style = 'style="width: 100%; padding: 0.75rem; border: 2px solid var(--border-color); border-radius: 8px; background: var(--input-bg); color: var(--text-primary);"'
    html_content = html_content.replace(pwd_input_style, 'class="password-input"')

    # Fix up the onclick elements that we didn't extract completely to keep risk low, as requested.

    # Ensure static dir exists
    if not os.path.exists('static'):
        os.makedirs('static')

    with open("static/dashboard.css", "w", encoding="utf-8") as f:
        f.write(css_content.strip())
        
    with open("static/dashboard.js", "w", encoding="utf-8") as f:
        f.write(js_content.strip())
        
    with open("templates/dashboard_refactored.html", "w", encoding="utf-8") as f:
        f.write(html_content.strip())

    print("Successfully refactored dashboard files.")

if __name__ == "__main__":
    refactor_dashboard()
