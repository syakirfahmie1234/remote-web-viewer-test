"""
Controller Application Entry Point.
Launches the PySide6 Graphical User Interface.
"""

import os
import sys
from PySide6.QtWidgets import QApplication

from controller.main_window import MainWindow
from dotenv import load_dotenv

load_dotenv()

SERVER_WS_URL = os.environ.get("SERVER_WS_URL", "ws://127.0.0.1:8000/ws/controller")
CONTROLLER_TOKEN = os.environ.get("CONTROLLER_TOKEN", "default-controller-token-secret")


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("RemoteWebsiteController")

    window = MainWindow(
        server_url=SERVER_WS_URL,
        token=CONTROLLER_TOKEN,
    )
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
