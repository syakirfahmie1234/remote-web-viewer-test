"""
WorkerTab Widget encapsulating the views and controls for a single worker.
"""
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QHBoxLayout, QButtonGroup, QScrollArea, QFrame, QLabel
from PySide6.QtCore import Qt
from controller.browser_view import BrowserView
from controller.command_panel import CommandPanel
from controller.statistics_panel import StatisticsPanel


class TabButtonWidget(QFrame):
    def __init__(self, title, handle, parent_tab):
        super().__init__()
        self.handle = handle
        self.parent_tab = parent_tab
        self.is_checked = False
        self.setCursor(Qt.PointingHandCursor)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 4, 2)
        layout.setSpacing(4)
        
        self.lbl = QLabel(title or "New Tab")
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(16, 16)
        self.close_btn.setCursor(Qt.ArrowCursor)
        
        layout.addWidget(self.lbl)
        layout.addWidget(self.close_btn)
        
        self.close_btn.clicked.connect(self._on_close)
        self.setChecked(False)
        
    def _on_close(self):
        self.parent_tab.browser_view.command_requested.emit("close_tab", {"handle": self.handle})

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.parent_tab.browser_view.command_requested.emit("switch_tab", {"handle": self.handle})

    def setChecked(self, checked: bool):
        self.is_checked = checked
        if checked:
            self.setStyleSheet("QFrame { background: #ffffff; border-top: 2px solid #0366d6; border-right: 1px solid #1b1f23; }")
            self.lbl.setStyleSheet("color: #24292e; font-size: 11px; background: transparent; border: none; font-weight: bold;")
            self.close_btn.setStyleSheet("QPushButton { border: none; border-radius: 8px; background: transparent; color: #24292e; font-weight: bold; } QPushButton:hover { background: #d73a49; color: white; }")
        else:
            self.setStyleSheet("QFrame { background: #1e2329; border-top: none; border-right: 1px solid #1b1f23; } QFrame:hover { background: #2d333b; }")
            self.lbl.setStyleSheet("color: #ffffff; font-size: 11px; background: transparent; border: none; font-weight: normal;")
            self.close_btn.setStyleSheet("QPushButton { border: none; border-radius: 8px; background: transparent; color: #ffffff; font-weight: bold; } QPushButton:hover { background: #d73a49; color: white; }")
class WorkerTab(QWidget):
    def __init__(self, worker_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.worker_id = worker_id
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        

        # Tab bar
        self.tab_bar_container = QWidget()
        self.tab_bar_container.setFixedHeight(28)
        self.tab_bar_container.setStyleSheet("background-color: #24292e; border-bottom: 1px solid #1b1f23;")
        self.tab_bar_layout = QHBoxLayout(self.tab_bar_container)
        self.tab_bar_layout.setContentsMargins(0, 0, 0, 0)
        self.tab_bar_layout.setSpacing(0)
        self.tab_bar_layout.setAlignment(Qt.AlignLeft)
        self.tab_bar_container.hide() # Hidden until >1 tab
        self.tab_buttons = {} # handle -> QPushButton

        self.browser_view = BrowserView()
        
        # Bottom Container for Controls & Stats
        self.bottom_container = QWidget()
        bottom_layout = QVBoxLayout(self.bottom_container)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        
        self.command_panel = CommandPanel()
        self.stats_panel = StatisticsPanel()
        
        bottom_layout.addWidget(self.command_panel)
        bottom_layout.addWidget(self.stats_panel)
        
        # Toggle Button
        self.toggle_bottom_btn = QPushButton("▼ Controls & Stats ▼")
        self.toggle_bottom_btn.setStyleSheet(
            "QPushButton { background: #e1e4e8; color: #24292e; border: none; border-top: 1px solid #d1d5da; padding: 4px; font-weight: bold; font-size: 11px; }"
            "QPushButton:hover { background: #d1d5da; }"
        )
        self.toggle_bottom_btn.setCursor(Qt.PointingHandCursor)
        self.bottom_expanded = True
        self.toggle_bottom_btn.clicked.connect(self._toggle_bottom_panel)
        
        layout.addWidget(self.tab_bar_container)
        layout.addWidget(self.browser_view, stretch=1)
        layout.addWidget(self.toggle_bottom_btn)
        layout.addWidget(self.bottom_container)

    def _toggle_bottom_panel(self) -> None:
        if self.bottom_expanded:
            self.bottom_container.hide()
            self.toggle_bottom_btn.setText("▲ Controls & Stats ▲")
        else:
            self.bottom_container.show()
            self.toggle_bottom_btn.setText("▼ Controls & Stats ▼")
        self.bottom_expanded = not self.bottom_expanded


    def add_browser_tab(self, handle: str, title: str) -> None:
        if handle in self.tab_buttons:
            self.tab_buttons[handle].lbl.setText(title or "New Tab")
            return
        btn = TabButtonWidget(title, handle, self)
        self.tab_bar_layout.addWidget(btn)
        self.tab_buttons[handle] = btn
        if len(self.tab_buttons) > 1:
            self.tab_bar_container.show()
            
    def remove_browser_tab(self, handle: str) -> None:
        if handle in self.tab_buttons:
            btn = self.tab_buttons.pop(handle)
            self.tab_bar_layout.removeWidget(btn)
            btn.deleteLater()
        if len(self.tab_buttons) <= 1:
            self.tab_bar_container.hide()
            
    def set_active_browser_tab(self, handle: str) -> None:
        for h, btn in self.tab_buttons.items():
            btn.setChecked(h == handle)
