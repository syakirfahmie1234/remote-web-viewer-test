"""
Main PySide6 Application Window for Remote Website Controller.
Integrates Worker List sidebar, BrowserView, CommandPanel, CommandQueue, and StatisticsPanel.
Renders and interacts with remote browsers exclusively through WebSocket relay.
"""

from __future__ import annotations
import logging
from typing import Optional
from typing import Dict, Optional

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QListWidget,
    QListWidgetItem,
    QGroupBox,
    QMessageBox,
    QTabWidget,
    QPushButton,
    QSizePolicy,
)

from controller.state_manager import ControllerStateManager
from controller.worker_manager import ControllerWorkerManager
from controller.websocket_client import ControllerWebSocketClient
from controller.command_queue import ControllerCommandQueue, CommandItem
from controller.browser_view import BrowserView
from controller.command_panel import CommandPanel
from controller.statistics_panel import StatisticsPanel
from controller.worker_tab import WorkerTab

from shared.protocol import (
    STATUS_CONNECTED,
    STATUS_DISCONNECTED,
    MSG_WORKER_STATUS,
    MSG_FULL_SNAPSHOT,
    MSG_DOM_UPDATE,
    MSG_COMMAND_RESULT,
    MSG_ERROR,
    CMD_HIGHLIGHT,
)
from shared.models import (
    BaseMessage,
    WorkerStatusMessage,
    FullSnapshotMessage,
    DomUpdateMessage,
    CommandResultMessage,
    ErrorMessage,
)
from shared.messages import (
    create_resync_request,
    create_controller_register,
    create_throttle_config,
)
from shared.throttle import get_profile

logger = logging.getLogger("controller.main_window")


class MainWindow(QMainWindow):
    """
    Main PySide6 Window for remote website interaction.
    """
    def __init__(
        self,
        server_url: str = "ws://127.0.0.1:8000/ws/controller",
        token: str = "default-controller-token-secret",
        client_id: str = "controller-gui",
    ) -> None:
        super().__init__()
        self.setWindowTitle("Remote Website Controller")
        self.resize(1280, 850)

        # Core State & Logic singletons
        self.state_mgr = ControllerStateManager()
        self.worker_mgr = ControllerWorkerManager()
        self.ws_client = ControllerWebSocketClient(
            server_url=server_url,
            token=token,
            client_id=client_id,
        )
        self.command_queue = ControllerCommandQueue(
            send_message_fn=self.ws_client.send_message
        )

        self._setup_ui()
        self._wire_signals()

        # Start background WebSocket client
        self.ws_client.start()

    def _setup_ui(self) -> None:
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        root_layout = QHBoxLayout(central_widget)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(0)

        # 1. Left Sidebar: Worker List
        self._left_panel = QWidget()
        self._left_panel.setFixedWidth(200)
        left_layout = QVBoxLayout(self._left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        workers_box = QGroupBox("Connected Workers")
        wb_layout = QVBoxLayout(workers_box)
        self.worker_list = QListWidget()
        self.worker_list.setStyleSheet(
            "QListWidget::item { padding: 8px; border-bottom: 1px solid #eee; font-family: monospace; font-size: 12px; }"
            "QListWidget::item:selected { background: #0366d6; color: white; }"
        )
        wb_layout.addWidget(self.worker_list)
        left_layout.addWidget(workers_box)
        root_layout.addWidget(self._left_panel)

        # 1.5. Sidebar Toggle Strip
        self._toggle_btn = QPushButton("◀")
        self._toggle_btn.setToolTip("Collapse worker list")
        self._toggle_btn.setFixedWidth(16)
        self._toggle_btn.setStyleSheet(
            "QPushButton { background: #e1e4e8; border: none; border-right: 1px solid #d1d5da; color: #24292e; border-radius: 0px; }"
            "QPushButton:hover { background: #d1d5da; }"
        )
        self._toggle_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._sidebar_expanded = True
        self._toggle_btn.clicked.connect(self._toggle_sidebar)
        root_layout.addWidget(self._toggle_btn)

        # 2. Right Main Area: Tabs for Workers
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._on_tab_close_requested)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        root_layout.addWidget(self.tabs, stretch=1)

        self.worker_tabs: Dict[str, WorkerTab] = {}

    def _toggle_sidebar(self) -> None:
        if self._sidebar_expanded:
            self._left_panel.setFixedWidth(0)
            self._toggle_btn.setText("▶")
        else:
            self._left_panel.setFixedWidth(200)
            self._toggle_btn.setText("◀")
        self._sidebar_expanded = not self._sidebar_expanded

    @property
    def browser_view(self) -> Optional[BrowserView]:
        active_id = self.worker_mgr.active_worker_id
        if active_id and active_id in self.worker_tabs:
            return self.worker_tabs[active_id].browser_view
        return None

    @property
    def command_panel(self) -> Optional[CommandPanel]:
        active_id = self.worker_mgr.active_worker_id
        if active_id and active_id in self.worker_tabs:
            return self.worker_tabs[active_id].command_panel
        return None

    @property
    def stats_panel(self) -> Optional[StatisticsPanel]:
        active_id = self.worker_mgr.active_worker_id
        if active_id and active_id in self.worker_tabs:
            return self.worker_tabs[active_id].stats_panel
        return None

    def _wire_signals(self) -> None:
        # WebSocket Client Signals
        self.ws_client.connected.connect(self._on_server_connected)
        self.ws_client.disconnected.connect(self._on_server_disconnected)
        self.ws_client.message_received.connect(self._on_message_received)
        self.ws_client.connection_error.connect(self._on_connection_error)

        # Worker Manager Signals
        self.worker_mgr.workers_updated.connect(self._refresh_worker_list_ui)
        self.worker_mgr.active_worker_changed.connect(self._on_active_worker_changed)
        self.worker_list.itemClicked.connect(self._on_worker_item_clicked)

        # UI Signals
        self.worker_list.itemClicked.connect(self._on_worker_item_clicked)
        self.command_queue.command_failed.connect(self._on_command_failed)
        self.command_queue.queue_updated.connect(self._on_queue_updated)

        # Global shortcuts
        shortcut_highlight = QShortcut(QKeySequence("Alt+H"), self)
        shortcut_highlight.activated.connect(
            lambda: self.command_panel.btn_highlight.click() if self.command_panel else None
        )

    # Slots & Event Handlers

    @Slot()
    def _on_server_connected(self) -> None:
        self.state_mgr.mark_all_stale()
        self._refresh_worker_list_ui()
        self._update_server_subscriptions()

        for w_id, tab in self.worker_tabs.items():
            tab.stats_panel.append_log("Connected to server.", "SYS")
            self.ws_client.send_message(
                create_resync_request(worker_id=w_id, reason="controller_reconnect")
            )

    @Slot()
    def _on_server_disconnected(self) -> None:
        if self.stats_panel is not None:
            self.stats_panel.append_log("Disconnected from relay server", "WARN")

    @Slot(str)
    def _on_connection_error(self, err: str) -> None:
        if self.stats_panel is not None:
            self.stats_panel.append_log(f"Connection error: {err}", "ERROR")

    @Slot(object)
    def _on_message_received(self, msg) -> None:
        worker_id = getattr(msg, "worker_id", "")
        tab = self.worker_tabs.get(worker_id)
        
        if type(msg).__name__ == "WorkerStatusMessage":
            self.worker_mgr.update_worker_status(msg.worker_id, msg.status, msg.dom_version)
            self.state_mgr.update_status(msg.worker_id, msg.status, msg.dom_version)
            if tab:
                tab.stats_panel.append_log(f"Worker '{msg.worker_id}' status: {msg.status}", "STATUS")
            self._refresh_worker_list_ui()

        elif type(msg).__name__ == "TabOpenedMessage":
            self.state_mgr.apply_tab_opened(msg)
            if tab:
                tab.add_browser_tab(msg.tab_handle, msg.tab_title)
                tab.set_active_browser_tab(msg.tab_handle)
                tab.stats_panel.append_log(f"Tab Opened: {msg.tab_handle} ({msg.tab_title})", "INFO")

        elif type(msg).__name__ == "TabClosedMessage":
            self.state_mgr.apply_tab_closed(msg)
            if tab:
                tab.remove_browser_tab(msg.tab_handle)
                tab.stats_panel.append_log(f"Tab Closed: {msg.tab_handle}", "INFO")
                self._update_tab_view_from_state(msg.worker_id)

        elif type(msg).__name__ == "AlertOpenedMessage":
            slot = self.state_mgr.get_or_create_slot(msg.worker_id)
            slot.alert_text = msg.alert_text
            self.state_mgr.update_status(msg.worker_id, "alert_blocking", None)
            if tab:
                tab.stats_panel.append_log(f"ALERT DETECTED on '{msg.worker_id}': {msg.alert_text}", "ALERT")
                self._update_tab_view_from_state(msg.worker_id)
            self._refresh_worker_list_ui()

        elif type(msg).__name__ == "TabOpenedMessage":
            self.state_mgr.apply_tab_opened(msg)
            if tab:
                tab.add_browser_tab(msg.tab_handle, msg.tab_title)
                tab.set_active_browser_tab(msg.tab_handle)
                tab.stats_panel.append_log(f"Tab Opened: {msg.tab_handle} ({msg.tab_title})", "INFO")

        elif type(msg).__name__ == "TabClosedMessage":
            self.state_mgr.apply_tab_closed(msg)
            if tab:
                tab.remove_browser_tab(msg.tab_handle)
                tab.stats_panel.append_log(f"Tab Closed: {msg.tab_handle}", "INFO")
                self._update_tab_view_from_state(msg.worker_id)

        elif type(msg).__name__ == "AlertOpenedMessage":
            slot = self.state_mgr.get_or_create_slot(msg.worker_id)
            slot.alert_text = msg.alert_text
            self.state_mgr.update_status(msg.worker_id, "alert_blocking", None)
            if tab:
                tab.stats_panel.append_log(f"ALERT DETECTED on '{msg.worker_id}': {msg.alert_text}", "ALERT")
                self._update_tab_view_from_state(msg.worker_id)
            self._refresh_worker_list_ui()

        elif type(msg).__name__ == "FullSnapshotMessage":
            byte_size = len(msg.html)  # Rough payload size estimate
            self.state_mgr.record_bytes_received(msg.worker_id, byte_size)
            self.state_mgr.apply_full_snapshot(msg)
            self.worker_mgr.update_worker_status(msg.worker_id, "connected", msg.version)
            self.state_mgr.update_status(msg.worker_id, "connected", msg.version)
            if tab:
                if msg.tab_handle:
                    tab.add_browser_tab(msg.tab_handle, msg.title)
                    tab.set_active_browser_tab(msg.tab_handle)
                tab.stats_panel.append_log(f"FULL_SNAPSHOT received from '{msg.worker_id}' (v0{msg.version})", "SNAPSHOT")
                tab.stats_panel.update_metrics(self.state_mgr.get_slot(msg.worker_id))
                self._update_tab_view_from_state(msg.worker_id)
            self._refresh_worker_list_ui()

        elif type(msg).__name__ == "DomUpdateMessage":
            byte_size = sum(len(str(op)) for op in msg.ops) * 20  # Rough heuristic
            self.state_mgr.record_bytes_received(msg.worker_id, byte_size)
            applied = self.state_mgr.apply_dom_update(msg)
            if not applied:
                if tab:
                    tab.stats_panel.append_log(f"Version mismatch for '{msg.worker_id}'. Requesting resync.", "WARN")
                self.ws_client.send_message(
                    create_resync_request(worker_id=msg.worker_id, reason="version_mismatch")
                )
            else:
                self.worker_mgr.update_worker_status(msg.worker_id, "connected", msg.version)
                if tab:
                    tab.stats_panel.append_log(f"DOM_UPDATE applied for '{msg.worker_id}' (v0{msg.version})", "UPDATE")
                    tab.stats_panel.update_metrics(self.state_mgr.get_slot(msg.worker_id))
                    self._update_tab_view_from_state(msg.worker_id)
                self._refresh_worker_list_ui()

        elif type(msg).__name__ == "CommandResultMessage":
            self.command_queue.handle_command_result(msg)
            self.state_mgr.record_command_result(msg)
            if msg.success and msg.command == "switch_tab":
                self._update_tab_view_from_state(msg.worker_id)
            status_text = "SUCCESS" if msg.success else f"FAILED: {msg.error}"
            if tab:
                tab.stats_panel.append_log(f"Command '{msg.command}' on '{msg.worker_id}': {status_text}", "RESULT")
                if msg.payload and "screenshot_base64" in msg.payload:
                    tab.browser_view.show_screenshot(msg.payload["screenshot_base64"])

        elif type(msg).__name__ == "ErrorMessage":
            if tab:
                tab.stats_panel.append_log(f"Error from '{msg.worker_id}': {msg.code} - {msg.detail}", "ERROR")

    @Slot(QListWidgetItem)
    def _on_worker_item_clicked(self, item: QListWidgetItem) -> None:
        worker_id = item.data(Qt.UserRole)
        if worker_id:
            if worker_id in self.worker_tabs:
                self.tabs.setCurrentWidget(self.worker_tabs[worker_id])
            else:
                tab = self._create_worker_tab(worker_id)
                self.tabs.setCurrentWidget(tab)
                self._update_server_subscriptions()
                
                slot = self.state_mgr.get_slot(worker_id)
                if slot is None or slot.is_stale:
                    self.ws_client.send_message(
                        create_resync_request(worker_id=worker_id, reason="tab_opened")
                    )

    @Slot(str)
    def _on_active_worker_changed(self, new_worker_id: str) -> None:
        if not new_worker_id:
            return
            
        if new_worker_id not in self.worker_tabs:
            tab = self._create_worker_tab(new_worker_id)
            self.tabs.setCurrentWidget(tab)
            self._update_server_subscriptions()
            
            slot = self.state_mgr.get_slot(new_worker_id)
            if slot is None or slot.is_stale:
                self.ws_client.send_message(
                    create_resync_request(worker_id=new_worker_id, reason="worker_switch_stale")
                )
        else:
            self.tabs.setCurrentWidget(self.worker_tabs[new_worker_id])
            
        tab = self.worker_tabs.get(new_worker_id)

        self._update_tab_view_from_state(new_worker_id)
        self._on_queue_updated(new_worker_id)

    def _update_tab_view_from_state(self, worker_id: str) -> None:
        tab = self.worker_tabs.get(worker_id)
        if not tab:
            return

        slot = self.state_mgr.get_slot(worker_id)
        if slot:
            active_tab = slot.active_tab
            if active_tab:
                tab.browser_view.update_view(
                    url=active_tab.url,
                    title=active_tab.title,
                    raw_html=active_tab.html,
                    dom_version=active_tab.dom_version,
                    is_stale=active_tab.is_stale,
                    worker_status=slot.status,
                    tab_handle=slot.active_tab_handle,
                )
                tab.set_active_browser_tab(slot.active_tab_handle)
            else:
                tab.browser_view.update_view("", "", "", 0, True, slot.status)
                
            if slot.status == "alert_blocking" and slot.alert_text is not None:
                tab.browser_view.show_alert(slot.alert_text)
            else:
                tab.browser_view.hide_alert()
            tab.browser_view.set_url(slot.url)

    @Slot(str)
    def _on_queue_updated(self, worker_id: str) -> None:
        tab = self.worker_tabs.get(worker_id)
        if tab:
            items = self.command_queue.get_items_for_worker(worker_id)
            tab.command_panel.update_queue_display(items)

    @Slot(object)
    def _on_command_failed(self, item) -> None:
        tab = self.worker_tabs.get(item.worker_id)
        if tab:
            tab.command_panel.show_failure(item)

    def _create_worker_tab(self, worker_id: str):
        tab = WorkerTab(worker_id)
        tab.command_panel.command_requested.connect(
            lambda cmd, pl, w_id=worker_id: self._on_command_requested_tab(w_id, cmd, pl)
        )
        tab.browser_view.command_requested.connect(
            lambda cmd, pl, w_id=worker_id: self._on_command_requested_tab(w_id, cmd, pl)
        )
        tab.browser_view.resync_requested.connect(
            lambda w_id=worker_id: self._on_resync_requested_tab(w_id)
        )
        tab.browser_view.navigate_requested.connect(
            lambda url, w_id=worker_id: self._on_command_requested_tab(w_id, "navigate", {"url": url})
        )
        tab.browser_view.new_tab_requested.connect(
            lambda url, w_id=worker_id: self._on_command_requested_tab(w_id, "new_tab", {"url": url})
        )
        tab.command_panel.resync_requested.connect(
            lambda w_id=worker_id: self._on_resync_requested_tab(w_id)
        )
        tab.command_panel.throttle_profile_changed.connect(
            lambda profile_name, w_id=worker_id: self._on_throttle_profile_changed_tab(w_id, profile_name)
        )
        tab.command_panel.browser_config_requested.connect(
            lambda headless, proxy_url, w_id=worker_id: self._on_browser_config_requested_tab(w_id, headless, proxy_url)
        )
        self.worker_tabs[worker_id] = tab
        self.tabs.addTab(tab, worker_id)
        return tab

    @Slot(str, bool, str)
    def _on_browser_config_requested_tab(self, worker_id: str, headless: bool, proxy_url: Optional[str]) -> None:
        from shared.messages import create_browser_config
        msg = create_browser_config(
            worker_id=worker_id,
            headless=headless,
            proxy_url=proxy_url,
        )
        self.ws_client.send_message(msg)
        
        tab = self.worker_tabs.get(worker_id)
        if tab:
            proxy_str = proxy_url if proxy_url else "none"
            tab.stats_panel.append_log(f"Requested browser config (headless={headless}, proxy={proxy_str})", "CONFIG")

    @Slot(str, str)
    def _on_throttle_profile_changed_tab(self, worker_id: str, profile_name: str) -> None:
        profile = get_profile(profile_name)
        if not profile:
            return
            
        msg = create_throttle_config(
            worker_id=worker_id,
            profile_name=profile.name,
            compression_level=profile.compression_level,
            compression_threshold=profile.compression_threshold,
            max_snapshot_bytes=profile.max_snapshot_bytes,
            min_snapshot_interval_ms=profile.min_snapshot_interval_ms,
        )
        self.ws_client.send_message(msg)
        
        tab = self.worker_tabs.get(worker_id)
        if tab:
            tab.stats_panel.append_log(f"Requested throttle profile '{profile_name}'", "THROTTLE")

    def _update_server_subscriptions(self) -> None:
        subs = list(self.worker_tabs.keys())
        msg = create_controller_register(
            client_id=self.ws_client.client_id,
            subscribed_worker_ids=subs
        )
        self.ws_client.send_message(msg)

    @Slot(int)
    def _on_tab_close_requested(self, index: int) -> None:
        tab = self.tabs.widget(index)
        if hasattr(tab, "worker_id"):
            worker_id = tab.worker_id
            self.tabs.removeTab(index)
            tab.deleteLater()
            if worker_id in self.worker_tabs:
                del self.worker_tabs[worker_id]
            self._update_server_subscriptions()
            
            if self.tabs.count() == 0:
                self.worker_mgr.select_worker("")
                self._refresh_worker_list_ui()

    @Slot(int)
    def _on_tab_changed(self, index: int) -> None:
        tab = self.tabs.widget(index)
        if hasattr(tab, "worker_id"):
            self.worker_mgr.select_worker(tab.worker_id)
            self._refresh_worker_list_ui()

    @Slot(str, str, dict)
    def _on_command_requested_tab(self, worker_id: str, command: str, payload: dict) -> None:
        self.state_mgr.record_command_sent(worker_id)
        # Rough heuristic for command byte size
        byte_size = len(command) + len(str(payload)) + 50
        self.state_mgr.record_bytes_sent(worker_id, byte_size)
        
        self.command_queue.enqueue_command(worker_id=worker_id, command=command, payload=payload)
        tab = self.worker_tabs.get(worker_id)
        if tab:
            tab.stats_panel.append_log(f"Queued '{command}' for '{worker_id}'", "COMMAND")
            tab.stats_panel.update_metrics(self.state_mgr.get_slot(worker_id))

    @Slot(str)
    def _on_resync_requested_tab(self, worker_id: str) -> None:
        self.ws_client.send_message(
            create_resync_request(worker_id=worker_id, reason="manual_request")
        )
    @Slot()
    def _refresh_worker_list_ui(self) -> None:
        self.worker_list.clear()
        workers = self.worker_mgr.get_known_workers()
        active_id = self.worker_mgr.active_worker_id

        for worker in workers:
            label = f"{worker['worker_id']} [{worker['status']}]"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, worker['worker_id'])
            
            if worker['worker_id'] == active_id:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setBackground(Qt.yellow)
                item.setForeground(Qt.black)

            self.worker_list.addItem(item)

    def closeEvent(self, event) -> None:
        """Clean shutdown on window close."""
        self.ws_client.stop()
        super().closeEvent(event)
