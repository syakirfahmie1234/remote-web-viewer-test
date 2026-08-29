"""
Browser View widget for PySide6 Controller.
Displays the remote website's synchronized DOM, URL, page title, and status for the active Worker.
"""

from __future__ import annotations
import base64
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QScrollArea,
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView

from controller.dom_renderer import DOMRenderer
from shared.protocol import STATUS_CONNECTED


class BrowserView(QWidget):
    """
    Renders the synchronized remote browser page and screenshot preview for the active Worker.
    """
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

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

        # Stacked view: Index 0 = HTML view, Index 1 = Screenshot view
        self.stack = QStackedWidget()

        # HTML Viewer
        self.html_viewer = QWebEngineView()
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

    def update_view(
        self,
        url: str,
        title: str,
        raw_html: str,
        dom_version: int,
        is_stale: bool,
        worker_status: str,
    ) -> None:
        """Update view with active worker's synchronized DOM state."""
        self.title_label.setText(f"<b>{title or 'Untitled Page'}</b>")
        self.url_label.setText(url or "")

        # Update status badge
        if worker_status != STATUS_CONNECTED:
            self.status_badge.setText(f"Worker {worker_status.upper()}")
            self.status_badge.setStyleSheet("background: #d73a49; color: white; padding: 2px 6px; border-radius: 3px;")
        elif is_stale:
            self.status_badge.setText("STALE — Resync Needed")
            self.status_badge.setStyleSheet("background: #f66a0a; color: white; padding: 2px 6px; border-radius: 3px;")
        else:
            self.status_badge.setText(f"Synchronized (v{dom_version})")
            self.status_badge.setStyleSheet("background: #28a745; color: white; padding: 2px 6px; border-radius: 3px;")

        # Render HTML
        formatted_html = DOMRenderer.prepare_html_for_view(raw_html, title=title, url=url)
        base_url = QUrl(url) if url else QUrl()
        self.html_viewer.setHtml(formatted_html, base_url)
        self.stack.setCurrentIndex(0)

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
