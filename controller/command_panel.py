"""
Command Panel Widget for Controller UI.
Provides navigation bar, browser controls (back/forward/refresh/resync/screenshot),
DOM element interaction tools (click/type/clear), and a live Command Queue table with failure alert display.
Always emits command requests intended for the active worker_id.
"""

from __future__ import annotations
from typing import List, Optional
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QComboBox,
    QGroupBox,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFrame,
)

from controller.command_queue import (
    CommandItem,
    STATE_QUEUED,
    STATE_IN_FLIGHT,
    STATE_SUCCESS,
    STATE_FAILED,
    STATE_TIMED_OUT,
)
from shared.protocol import (
    CMD_NAVIGATE,
    CMD_CLICK,
    CMD_TYPE,
    CMD_CLEAR,
    CMD_BACK,
    CMD_FORWARD,
    CMD_REFRESH,
    CMD_SCREENSHOT,
    CMD_HIGHLIGHT,
)


class CommandPanel(QWidget):
    """
    Panel providing remote control actions and a live per-Worker command queue visualizer.
    """
    command_requested = Signal(str, dict)
    resync_requested = Signal()
    throttle_profile_changed = Signal(str)  # Emits profile_name
    browser_config_requested = Signal(bool, str)  # Emits (headless, proxy_url)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # 1. Error Banner (Hidden by default, shown on failure)
        self.error_banner = QFrame()
        self.error_banner.setStyleSheet(
            "background-color: #ffeef0; border: 1px solid #fdaeb7; border-radius: 4px; padding: 6px;"
        )
        eb_layout = QHBoxLayout(self.error_banner)
        eb_layout.setContentsMargins(8, 4, 8, 4)
        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #cb2431; font-weight: bold;")
        self.btn_dismiss_error = QPushButton("✕")
        self.btn_dismiss_error.setFixedWidth(24)
        self.btn_dismiss_error.setStyleSheet("background: transparent; border: none; font-weight: bold; color: #cb2431;")
        self.btn_dismiss_error.clicked.connect(self.error_banner.hide)
        eb_layout.addWidget(self.error_label, stretch=1)
        eb_layout.addWidget(self.btn_dismiss_error)
        self.error_banner.hide()
        main_layout.addWidget(self.error_banner)

        # 2. Navigation Bar
        nav_layout = QHBoxLayout()
        self.btn_back = QPushButton("◀ Back")
        self.btn_forward = QPushButton("Forward ▶")
        self.btn_refresh = QPushButton("⟳ Refresh")
        self.btn_resync = QPushButton("⚡ Resync")
        self.btn_screenshot = QPushButton("📷 Screenshot")

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://example.com/...")
        self.btn_navigate = QPushButton("Go")

        nav_layout.addWidget(self.btn_back)
        nav_layout.addWidget(self.btn_forward)
        nav_layout.addWidget(self.btn_refresh)
        nav_layout.addWidget(self.btn_resync)
        nav_layout.addWidget(self.btn_screenshot)
        nav_layout.addWidget(self.url_input, stretch=1)
        nav_layout.addWidget(self.btn_navigate)
        main_layout.addLayout(nav_layout)

        # 3. Element Interaction & Queue Split
        interaction_layout = QHBoxLayout()

        # Element Controls
        elem_box = QGroupBox("DOM Element Action")
        elem_layout = QVBoxLayout(elem_box)

        sel_row = QHBoxLayout()
        self.selector_input = QLineEdit()
        self.selector_input.setPlaceholderText("CSS Selector (e.g. #search, .btn-primary)")
        sel_row.addWidget(QLabel("Selector:"))
        sel_row.addWidget(self.selector_input)
        elem_layout.addLayout(sel_row)

        val_row = QHBoxLayout()
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("Text to type...")
        val_row.addWidget(QLabel("Value:"))
        val_row.addWidget(self.text_input)
        elem_layout.addLayout(val_row)

        btn_row = QHBoxLayout()
        self.btn_click = QPushButton("Click")
        self.btn_type = QPushButton("Type")
        self.btn_clear = QPushButton("Clear")
        self.btn_highlight = QPushButton("🔆 Highlight")
        self.btn_highlight.setToolTip(
            "Briefly highlight the element matching the CSS selector on the Worker browser.\n"
            "Shortcut: Alt+H"
        )
        self.btn_highlight.setStyleSheet(
            "QPushButton { color: #FF6B00; font-weight: bold; border: 1px solid #FF6B00; border-radius: 4px; padding: 3px 8px; }"
            "QPushButton:hover { background-color: #fff3e0; }"
            "QPushButton:pressed { background-color: #ffe0b2; }"
        )
        btn_row.addWidget(self.btn_click)
        btn_row.addWidget(self.btn_type)
        btn_row.addWidget(self.btn_clear)
        btn_row.addWidget(self.btn_highlight)
        elem_layout.addLayout(btn_row)

        profile_row = QHBoxLayout()
        self.profile_combo = QComboBox()
        self.profile_combo.addItems(["realtime", "balanced", "low_bandwidth"])
        self.profile_combo.setCurrentText("balanced")
        profile_row.addWidget(QLabel("Throttle Profile:"))
        profile_row.addWidget(self.profile_combo, stretch=1)
        elem_layout.addLayout(profile_row)
        
        # Browser Config Row
        config_row = QHBoxLayout()
        from PySide6.QtWidgets import QCheckBox
        self.chk_headless = QCheckBox("Headless")
        self.chk_headless.setChecked(True)
        self.chk_headless.setToolTip("Toggle headless mode. Applies instantly and restarts worker browser.")
        
        self.proxy_input = QLineEdit()
        self.proxy_input.setPlaceholderText("Proxy URL (e.g. socks5://1.2.3.4:1080)")
        self.proxy_input.setToolTip("Leave blank for no proxy. Applies instantly and restarts worker browser.")
        
        self.btn_apply_config = QPushButton("Apply Config")
        
        config_row.addWidget(self.chk_headless)
        config_row.addWidget(self.proxy_input, stretch=1)
        config_row.addWidget(self.btn_apply_config)
        elem_layout.addLayout(config_row)

        interaction_layout.addWidget(elem_box, stretch=2)

        # Command Queue Table
        queue_box = QGroupBox("Sequential Command Queue")
        queue_layout = QVBoxLayout(queue_box)

        self.queue_table = QTableWidget()
        self.queue_table.setColumnCount(4)
        self.queue_table.setHorizontalHeaderLabels(["Time", "Command", "State", "Duration"])
        self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.queue_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.queue_table.setMaximumHeight(140)
        self.queue_table.setStyleSheet("font-family: monospace; font-size: 11px;")
        queue_layout.addWidget(self.queue_table)

        interaction_layout.addWidget(queue_box, stretch=3)
        main_layout.addLayout(interaction_layout)

        # Connect button signals
        self.btn_navigate.clicked.connect(self._on_navigate_clicked)
        self.url_input.returnPressed.connect(self._on_navigate_clicked)
        self.btn_back.clicked.connect(lambda: self.command_requested.emit(CMD_BACK, {}))
        self.btn_forward.clicked.connect(lambda: self.command_requested.emit(CMD_FORWARD, {}))
        self.btn_refresh.clicked.connect(lambda: self.command_requested.emit(CMD_REFRESH, {}))
        self.btn_screenshot.clicked.connect(lambda: self.command_requested.emit(CMD_SCREENSHOT, {}))
        self.btn_resync.clicked.connect(lambda: self.resync_requested.emit())

        self.btn_click.clicked.connect(self._on_click_clicked)
        self.btn_type.clicked.connect(self._on_type_clicked)
        self.btn_clear.clicked.connect(self._on_clear_clicked)
        self.btn_highlight.clicked.connect(self._on_highlight_clicked)
        self.profile_combo.currentTextChanged.connect(self.throttle_profile_changed.emit)
        self.btn_apply_config.clicked.connect(self._on_apply_config_clicked)

    def set_url(self, url: str) -> None:
        """Update URL field in navigation bar."""
        self.url_input.setText(url)

    def show_failure(self, item: CommandItem) -> None:
        """Surface a command failure prominently in the GUI."""
        self.error_label.setText(
            f"Command '{item.command}' failed on '{item.worker_id}': {item.error or 'Execution error'}"
        )
        self.error_banner.show()

    def update_queue_display(self, items: List[CommandItem]) -> None:
        """Render the list of command queue items into the table."""
        self.queue_table.setRowCount(len(items))

        for row, item in enumerate(items):
            # Time
            time_str = item.submitted_at.strftime("%H:%M:%S")
            self.queue_table.setItem(row, 0, QTableWidgetItem(time_str))

            # Command description
            desc = item.command
            if "selector" in item.payload:
                desc += f" ({item.payload['selector']})"
            elif "url" in item.payload:
                desc += f" ({item.payload['url']})"
            self.queue_table.setItem(row, 1, QTableWidgetItem(desc))

            # State Badge
            state_item = QTableWidgetItem(item.state)
            if item.state == STATE_SUCCESS:
                state_item.setForeground(Qt.darkGreen)
            elif item.state == STATE_IN_FLIGHT:
                state_item.setForeground(Qt.blue)
            elif item.state in (STATE_FAILED, STATE_TIMED_OUT):
                state_item.setForeground(Qt.red)
            else:
                state_item.setForeground(Qt.darkGray)
            self.queue_table.setItem(row, 2, state_item)

            # Duration
            dur_str = f"{item.duration_ms:.0f}ms" if item.duration_ms is not None else "-"
            self.queue_table.setItem(row, 3, QTableWidgetItem(dur_str))

    def _on_navigate_clicked(self) -> None:
        url = self.url_input.text().strip()
        if url:
            self.command_requested.emit(CMD_NAVIGATE, {"url": url})

    def _on_click_clicked(self) -> None:
        selector = self.selector_input.text().strip()
        if selector:
            self.command_requested.emit(CMD_CLICK, {"selector": selector})

    def _on_type_clicked(self) -> None:
        selector = self.selector_input.text().strip()
        text = self.text_input.text()
        if selector:
            self.command_requested.emit(CMD_TYPE, {"selector": selector, "text": text, "clear_first": True})

    def _on_clear_clicked(self) -> None:
        selector = self.selector_input.text().strip()
        if selector:
            self.command_requested.emit(CMD_CLEAR, {"selector": selector})

    def _on_highlight_clicked(self) -> None:
        """Dispatch a highlight command to briefly outline the selected DOM element."""
        selector = self.selector_input.text().strip()
        if selector:
            self.command_requested.emit(
                CMD_HIGHLIGHT,
                {"selector": selector, "duration_ms": 1500, "color": "#FF6B00"},
            )

    def _on_apply_config_clicked(self) -> None:
        headless = self.chk_headless.isChecked()
        proxy = self.proxy_input.text().strip()
        proxy_val = proxy if proxy else None
        self.browser_config_requested.emit(headless, proxy_val)
