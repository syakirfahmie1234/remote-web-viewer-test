"""
Statistics and Event Log Panel Widget for Controller UI.
Displays real-time per-Worker bandwidth metrics, sync counters, DOM version, and live event log.
"""

from __future__ import annotations
from datetime import datetime
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QGridLayout,
    QLabel,
    QGroupBox,
    QPlainTextEdit,
)

from controller.state_manager import WorkerStateSlot


class StatisticsPanel(QWidget):
    """
    Displays real-time bandwidth and operational metrics for the selected Worker and event logging.
    """
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # 1. Metrics Group Box
        metrics_box = QGroupBox("Worker Performance & Sync Metrics")
        grid = QGridLayout(metrics_box)

        self.lbl_worker_id = QLabel("None")
        self.lbl_dom_version = QLabel("v0")
        self.lbl_snapshots = QLabel("0")
        self.lbl_diffs = QLabel("0")
        self.lbl_commands = QLabel("0")
        self.lbl_failed_cmds = QLabel("0")
        self.lbl_bytes_recv = QLabel("0 B")
        self.lbl_bytes_sent = QLabel("0 B")

        # Style metric labels
        for lbl in (
            self.lbl_worker_id, self.lbl_dom_version, self.lbl_snapshots, 
            self.lbl_diffs, self.lbl_commands, self.lbl_failed_cmds,
            self.lbl_bytes_recv, self.lbl_bytes_sent
        ):
            lbl.setStyleSheet("font-weight: bold; color: #0366d6;")

        grid.addWidget(QLabel("Worker ID:"), 0, 0)
        grid.addWidget(self.lbl_worker_id, 0, 1)

        grid.addWidget(QLabel("DOM Version:"), 0, 2)
        grid.addWidget(self.lbl_dom_version, 0, 3)

        grid.addWidget(QLabel("Snapshots Received:"), 1, 0)
        grid.addWidget(self.lbl_snapshots, 1, 1)

        grid.addWidget(QLabel("Diff Updates:"), 1, 2)
        grid.addWidget(self.lbl_diffs, 1, 3)

        grid.addWidget(QLabel("Commands Executed:"), 2, 0)
        grid.addWidget(self.lbl_commands, 2, 1)

        grid.addWidget(QLabel("Failed Commands:"), 2, 2)
        grid.addWidget(self.lbl_failed_cmds, 2, 3)
        
        grid.addWidget(QLabel("Bandwidth Recv:"), 3, 0)
        grid.addWidget(self.lbl_bytes_recv, 3, 1)
        
        grid.addWidget(QLabel("Bandwidth Sent:"), 3, 2)
        grid.addWidget(self.lbl_bytes_sent, 3, 3)

        main_layout.addWidget(metrics_box)

        # 2. Event Log Box
        log_box = QGroupBox("Event Log")
        log_layout = QVBoxLayout(log_box)

        self.log_viewer = QPlainTextEdit()
        self.log_viewer.setReadOnly(True)
        self.log_viewer.setMaximumHeight(160)
        self.log_viewer.setStyleSheet("font-family: Consolas, monospace; font-size: 11px; background: #fafbfc;")

        log_layout.addWidget(self.log_viewer)
        main_layout.addWidget(log_box)

    def _format_bytes(self, num: int) -> str:
        """Format byte count into KB/MB string."""
        if num < 1024:
            return f"{num} B"
        elif num < 1024 * 1024:
            return f"{num / 1024:.1f} KB"
        else:
            return f"{num / (1024 * 1024):.2f} MB"

    def update_metrics(self, slot: WorkerStateSlot | None) -> None:
        """Update metrics labels with slot values."""
        if slot is None:
            self.lbl_worker_id.setText("None")
            self.lbl_dom_version.setText("v0")
            self.lbl_snapshots.setText("0")
            self.lbl_diffs.setText("0")
            self.lbl_commands.setText("0")
            self.lbl_failed_cmds.setText("0")
            self.lbl_bytes_recv.setText("0 B")
            self.lbl_bytes_sent.setText("0 B")
            return

        self.lbl_worker_id.setText(slot.worker_id)
        self.lbl_dom_version.setText(f"v{slot.dom_version}")
        self.lbl_snapshots.setText(str(slot.snapshots_received))
        self.lbl_diffs.setText(str(slot.diffs_received))
        self.lbl_commands.setText(str(slot.commands_sent))
        self.lbl_failed_cmds.setText(str(slot.commands_failed))
        self.lbl_bytes_recv.setText(self._format_bytes(slot.bytes_received))
        self.lbl_bytes_sent.setText(self._format_bytes(slot.bytes_sent))
        
        if slot.commands_failed > 0:
            self.lbl_failed_cmds.setStyleSheet("font-weight: bold; color: #d73a49;")
        else:
            self.lbl_failed_cmds.setStyleSheet("font-weight: bold; color: #28a745;")

    def append_log(self, text: str, level: str = "INFO") -> None:
        """Append timestamped entry to event log viewer."""
        now = datetime.now().strftime("%H:%M:%S")
        self.log_viewer.appendPlainText(f"[{now}] [{level}] {text}")
