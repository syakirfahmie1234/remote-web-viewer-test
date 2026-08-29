"""
WorkerTab Widget encapsulating the views and controls for a single worker.
"""
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout
from controller.browser_view import BrowserView
from controller.command_panel import CommandPanel
from controller.statistics_panel import StatisticsPanel

class WorkerTab(QWidget):
    def __init__(self, worker_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.worker_id = worker_id
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.browser_view = BrowserView()
        self.command_panel = CommandPanel()
        self.stats_panel = StatisticsPanel()
        layout.addWidget(self.browser_view, stretch=3)
        layout.addWidget(self.command_panel, stretch=0)
        layout.addWidget(self.stats_panel, stretch=1)
