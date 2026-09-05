"""
Browser View widget for PySide6 Controller.
Displays the remote website's synchronized DOM, URL, page title, and status for the active Worker.
"""

from __future__ import annotations
import base64
import json
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QScrollArea,
    QPushButton,
    QLineEdit
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage

from controller.dom_renderer import DOMRenderer
from shared.protocol import (
    STATUS_CONNECTED,
    CMD_CLICK,
    CMD_TYPE,
    CMD_SCROLL,
    CMD_ACCEPT_ALERT,
    CMD_DISMISS_ALERT,
    CMD_SEND_ALERT_TEXT
)

CLICK_INTERCEPTOR_JS = '''
<script>
(function() {
    if (window.__click_interceptor_installed) return;
    window.__click_interceptor_installed = true;

    function getUniqueSelector(el) {
        if (el.id) return '#' + el.id;
        var path = [];
        var current = el;
        while (current && current !== document.body && current !== document.documentElement) {
            var selector = current.tagName.toLowerCase();
            if (current.id) {
                path.unshift('#' + current.id);
                break;
            }
            var index = 1;
            var sibling = current.previousElementSibling;
            while (sibling) {
                index++;
                sibling = sibling.previousElementSibling;
            }
            selector += ':nth-child(' + index + ')';
            path.unshift(selector);
            current = current.parentElement;
        }
        return path.join(' > ');
    }

    document.addEventListener('click', function(e) {
        var target = e.target;
        var isClickable = false;
        var current = target;

        while (current && current !== document) {
            var tag = current.tagName ? current.tagName.toLowerCase() : '';
            if (!tag) {
                current = current.parentNode;
                continue;
            }
            var role = current.getAttribute('role');
            var type = current.getAttribute('type');
            var hasClick = current.hasAttribute('onclick') || current.hasAttribute('ng-click') || current.hasAttribute('@click') || current.hasAttribute('v-on:click');
            var style = window.getComputedStyle(current);
            var cursor = style ? style.cursor : '';

            if (
                tag === 'a' || tag === 'button' ||
                (tag === 'input' && (type === 'button' || type === 'submit' || type === 'reset' || type === 'checkbox' || type === 'radio')) ||
                role === 'button' || role === 'link' ||
                hasClick || cursor === 'pointer'
            ) {
                isClickable = true;
                target = current;
                break;
            }
            current = current.parentNode;
        }

        if (isClickable) {
            e.preventDefault();
            e.stopPropagation();
            var selector = getUniqueSelector(target);
            console.log("ANTIGRAVITY_CMD:remote-click:" + btoa(unescape(encodeURIComponent(selector))));
        }
    }, true);

    document.addEventListener('change', function(e) {
        var target = e.target;
        if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT') {
            var type = target.getAttribute('type');
            if (type !== 'button' && type !== 'submit' && type !== 'reset' && type !== 'checkbox' && type !== 'radio') {
                var selector = getUniqueSelector(target);
                var val = target.value;
                var payload = JSON.stringify({ selector: selector, text: val });
                console.log("ANTIGRAVITY_CMD:remote-type:" + btoa(unescape(encodeURIComponent(payload))));
            }
        }
    }, true);
})();
</script>
'''

class InterceptorPage(QWebEnginePage):
    def __init__(self, navigate_signal, browser_view_ref=None, parent=None):
        super().__init__(parent)
        self.navigate_signal = navigate_signal
        self.browser_view_ref = browser_view_ref

    def javaScriptConsoleMessage(self, level, msg, line, source):
        if msg.startswith("ANTIGRAVITY_CMD:"):
            url_str = msg[len("ANTIGRAVITY_CMD:"):]
            
            if url_str.startswith("remote-click:"):
                try:
                    encoded_selector = url_str.replace("remote-click:", "")
                    selector = base64.b64decode(encoded_selector).decode('utf-8')
                    if self.browser_view_ref:
                        self.browser_view_ref.add_click_effect(selector)
                        self.browser_view_ref.command_requested.emit(CMD_CLICK, {"selector": selector})
                except Exception as e:
                    print("Failed to decode remote click:", e)
            elif url_str.startswith("remote-type:"):
                try:
                    encoded_payload = url_str.replace("remote-type:", "")
                    decoded_str = base64.b64decode(encoded_payload).decode('utf-8')
                    payload = json.loads(decoded_str)
                    if self.browser_view_ref:
                        self.browser_view_ref.command_requested.emit(CMD_TYPE, {"selector": payload["selector"], "text": payload["text"], "clear_first": True})
                except Exception as e:
                    print("Failed to parse remote type:", e)
        else:
            super().javaScriptConsoleMessage(level, msg, line, source)

    def acceptNavigationRequest(self, url, _type, isMainFrame):
        if url.scheme() == "data":
            return True
            
        url_str = url.toString()
        if url_str.startswith("remote-click:") or url_str.startswith("remote-type:"):
            return False
            
        if url_str.startswith("remote-scroll:"):
            try:
                coords = url_str.replace("remote-scroll:", "").split(",")
                x, y = int(coords[0]), int(coords[1])
                if self.browser_view_ref:
                    self.browser_view_ref.command_requested.emit(CMD_SCROLL, {"x": x, "y": y, "absolute": True})
            except Exception as e:
                print("Failed to parse remote scroll:", e)
            return False
            
        self.navigate_signal.emit(url_str)
        return False

class CustomWebEngineView(QWebEngineView):
    new_tab_requested = Signal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._temp_views = []

    def createWindow(self, _type):
        temp_view = QWebEngineView()
        temp_page = InterceptorPage(self.new_tab_requested, None, temp_view)
        temp_view.setPage(temp_page)
        self._temp_views.append(temp_view)
        return temp_view

class BrowserView(QWidget):
    """
    Renders the synchronized remote browser page and screenshot preview for the active Worker.
    """
    command_requested = Signal(str, dict)
    resync_requested = Signal()
    navigate_requested = Signal(str)
    new_tab_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._live_browsing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # Header info bar (Title, URL, DOM Status)
        header_layout = QHBoxLayout()
        self.title_label = QLabel("<b>No Worker Selected</b>")
        self.url_label = QLabel("")
        self.url_label.setStyleSheet("color: #0366d6; font-family: monospace;")
        self.status_badge = QLabel("Offline")
        self.status_badge.setStyleSheet("padding: 2px 6px; border-radius: 3px; background: #e1e4e8; font-weight: bold;")

        header_layout.addWidget(self.title_label)
        header_layout.addWidget(self.url_label, stretch=1)
        header_layout.addWidget(self.status_badge)
        layout.addLayout(header_layout)

        # Navigation Bar
        nav_layout = QHBoxLayout()
        self.btn_back = QPushButton("◀ Back")
        self.btn_forward = QPushButton("Forward ▶")
        self.btn_refresh = QPushButton("⟳ Refresh")
        self.btn_resync = QPushButton("⚡ Resync")
        self.btn_screenshot = QPushButton("📷 Screenshot")
        self.btn_live = QPushButton("Live")
        self.btn_live.setCheckable(True)
        self.btn_live.setStyleSheet("QPushButton:checked { background-color: #28a745; color: white; font-weight: bold; }")

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://example.com/...")
        self.btn_navigate = QPushButton("Go")

        nav_layout.addWidget(self.btn_back)
        nav_layout.addWidget(self.btn_forward)
        nav_layout.addWidget(self.btn_refresh)
        nav_layout.addWidget(self.btn_resync)
        nav_layout.addWidget(self.btn_screenshot)
        nav_layout.addWidget(self.btn_live)
        nav_layout.addWidget(self.url_input, stretch=1)
        nav_layout.addWidget(self.btn_navigate)
        layout.addLayout(nav_layout)
        
        self.btn_navigate.clicked.connect(self._on_navigate_clicked)
        self.url_input.returnPressed.connect(self._on_navigate_clicked)
        self.btn_back.clicked.connect(lambda: self.command_requested.emit("back", {}))
        self.btn_forward.clicked.connect(lambda: self.command_requested.emit("forward", {}))
        self.btn_refresh.clicked.connect(lambda: self.command_requested.emit("refresh", {}))
        self.btn_resync.clicked.connect(lambda: self.resync_requested.emit())
        self.btn_screenshot.clicked.connect(lambda: self.command_requested.emit("screenshot", {}))
        self.btn_live.toggled.connect(self._on_live_toggled)


        # Alert Panel
        self.alert_panel = QWidget()
        self.alert_panel.setStyleSheet("background-color: #ffcccc; color: #000; border: 1px solid #ff0000; padding: 5px;")
        alert_layout = QVBoxLayout(self.alert_panel)
        
        self.alert_label = QLabel()
        self.alert_label.setWordWrap(True)
        alert_layout.addWidget(self.alert_label)
        
        self.alert_input = QLineEdit()
        alert_layout.addWidget(self.alert_input)
        
        btn_layout = QHBoxLayout()
        self.btn_accept_alert = QPushButton("Accept")
        self.btn_accept_alert.clicked.connect(self._on_accept_alert)
        self.btn_dismiss_alert = QPushButton("Dismiss")
        self.btn_dismiss_alert.clicked.connect(self._on_dismiss_alert)
        btn_layout.addWidget(self.btn_accept_alert)
        btn_layout.addWidget(self.btn_dismiss_alert)
        alert_layout.addLayout(btn_layout)
        
        self.alert_panel.hide()
        layout.addWidget(self.alert_panel)

        # Stacked view: Index 0 = HTML view, Index 1 = Screenshot view
        self.stack = QStackedWidget()

        # HTML Viewer
        self.html_viewer = CustomWebEngineView()
        self.interceptor_page = InterceptorPage(self.navigate_requested, self, self.html_viewer)
        self.html_viewer.setPage(self.interceptor_page)
        self.html_viewer.new_tab_requested.connect(self.new_tab_requested)
        self.html_viewer.setContextMenuPolicy(Qt.NoContextMenu)
        self.stack.addWidget(self.html_viewer)

        # Screenshot Viewer
        self.screenshot_label = QLabel()
        self.screenshot_label.setAlignment(Qt.AlignCenter)
        scroll_area = QScrollArea()
        scroll_area.setWidget(self.screenshot_label)
        scroll_area.setWidgetResizable(True)
        self.stack.addWidget(scroll_area)

        layout.addWidget(self.stack, stretch=1)


    def _on_navigate_clicked(self) -> None:
        url = self.url_input.text().strip()
        if url:
            self.command_requested.emit("navigate", {"url": url})
            
    def _on_live_toggled(self, checked: bool) -> None:
        self._live_browsing = checked
        if checked:
            # We want to actually load the real URL in QWebEngine instead of just dumb-shell
            # Wait, live browsing in QWebEngine connects directly to the internet.
            url = self.url_input.text().strip()
            if url:
                self.html_viewer.setUrl(QUrl(url))
        else:
            # Revert to dummy
            self.resync_requested.emit()

    def set_url(self, url: str) -> None:
        self.url_input.setText(url)
        self.url_label.setText(url)

    def show_alert(self, text: str) -> None:
        self.alert_label.setText(text)
        self.alert_input.clear()
        self.alert_panel.show()

    def hide_alert(self) -> None:
        self.alert_panel.hide()

    def _on_accept_alert(self) -> None:
        text = self.alert_input.text()
        if text:
            self.command_requested.emit(CMD_SEND_ALERT_TEXT, {"text": text})
        self.command_requested.emit(CMD_ACCEPT_ALERT, {})
        self.hide_alert()

    def _on_dismiss_alert(self) -> None:
        self.command_requested.emit(CMD_DISMISS_ALERT, {})
        self.hide_alert()

    def add_click_effect(self, selector: str) -> None:
        js = f"""
        (function() {{
            var el = document.querySelector('{selector}');
            if (el) {{
                var rect = el.getBoundingClientRect();
                var div = document.createElement('div');
                div.style.position = 'fixed';
                div.style.left = rect.left + 'px';
                div.style.top = rect.top + 'px';
                div.style.width = rect.width + 'px';
                div.style.height = rect.height + 'px';
                div.style.backgroundColor = 'rgba(255, 0, 0, 0.3)';
                div.style.border = '2px solid red';
                div.style.zIndex = '999999';
                div.style.pointerEvents = 'none';
                div.style.transition = 'all 0.5s ease-out';
                document.body.appendChild(div);
                setTimeout(() => {{ div.style.opacity = '0'; }}, 500);
                setTimeout(() => {{ if (div.parentNode) div.parentNode.removeChild(div); }}, 1000);
            }}
        }})();
        """
        self.html_viewer.page().runJavaScript(js)

    def update_view(
        self,
        url: str,
        title: str,
        raw_html: str,
        dom_version: int,
        is_stale: bool,
        worker_status: str,
        tab_handle: str = "",
    ) -> None:
        """Update view with active worker's synchronized DOM state."""
        self.title_label.setText(f"<b>{title or 'Untitled Page'}</b>")
        self.url_label.setText(url or "")

        # Update status badge
        if worker_status != STATUS_CONNECTED:
            self.status_badge.setText(f"Worker {worker_status.upper()}")
            self.status_badge.setStyleSheet("background: #d73a49; color: white; padding: 2px 6px; border-radius: 3px;")
        elif is_stale:
            self.status_badge.setText("STALE - Resync Needed")
            self.status_badge.setStyleSheet("background: #f66a0a; color: white; padding: 2px 6px; border-radius: 3px;")
        else:
            self.status_badge.setText(f"Synchronized (v{dom_version})")
            self.status_badge.setStyleSheet("background: #28a745; color: white; padding: 2px 6px; border-radius: 3px;")

        if getattr(self, "_live_browsing", False):
            return

        if not raw_html:
            self.resync_requested.emit()
            return

        formatted_html = DOMRenderer.prepare_html_for_view(raw_html, title=title, url=url)
        if getattr(self, '_live_browsing', False) == False:
            if "</body>" in formatted_html:
                formatted_html = formatted_html.replace("</body>", CLICK_INTERCEPTOR_JS + "\n</body>")
            else:
                formatted_html += CLICK_INTERCEPTOR_JS
                
        base_url = QUrl(url) if url else QUrl()
        
        def _do_update(res):
            scroll_x = res.get('x', 0) if res else 0
            scroll_y = res.get('y', 0) if res else 0
            
            restore_script = f"<script>window.scrollTo({scroll_x}, {scroll_y});</script>"
            final_html = formatted_html
            if "</body>" in final_html:
                final_html = final_html.replace("</body>", restore_script + "\n</body>")
            else:
                final_html += restore_script
                
            self.html_viewer.setHtml(final_html, base_url)
            self.stack.setCurrentIndex(0)
            
        self.html_viewer.page().runJavaScript("(function(){ return {x: window.scrollX, y: window.scrollY}; })();", 0, _do_update)

    def show_screenshot(self, b64_data: str) -> None:
        """Display screenshot data in the viewer."""
        try:
            img_bytes = base64.b64decode(b64_data)
            pixmap = QPixmap()
            pixmap.loadFromData(img_bytes)
            self.screenshot_label.setPixmap(pixmap)
            self.stack.setCurrentIndex(1)
        except Exception:
            pass
            
    def set_url(self, url: str) -> None:
        pass
