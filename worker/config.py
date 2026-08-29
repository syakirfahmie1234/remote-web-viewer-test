"""
Worker configuration and stable worker_id management.
Ensures worker_id is persistent across restarts/reconnects.
"""

import os
from pathlib import Path
from typing import Final
import uuid
from dotenv import load_dotenv

load_dotenv()


def get_or_create_stable_worker_id() -> str:
    """
    Get stable worker_id from environment variable WORKER_ID.
    If not provided in environment, read from or create a local .worker_id file.
    """
    env_id = os.environ.get("WORKER_ID")
    if env_id and env_id.strip():
        return env_id.strip()

    id_file = Path(".worker_id")
    if id_file.exists():
        try:
            saved_id = id_file.read_text(encoding="utf-8").strip()
            if saved_id:
                return saved_id
        except Exception:
            pass

    # Generate a new unique worker ID and persist it
    new_id = f"worker-{uuid.uuid4().hex[:8]}"
    try:
        id_file.write_text(new_id, encoding="utf-8")
    except Exception:
        pass
    return new_id


# Stable Worker Identity
WORKER_ID: Final[str] = get_or_create_stable_worker_id()

# Server WebSocket Endpoint
SERVER_WS_URL: Final[str] = os.environ.get("SERVER_WS_URL", "ws://127.0.0.1:8000/ws/worker")

# Worker Authentication Token
WORKER_TOKEN: Final[str] = os.environ.get("WORKER_TOKEN", "default-worker-token-secret")

# Target Website Domain Restriction
TARGET_DOMAIN: Final[str] = os.environ.get("TARGET_DOMAIN", "https://example.com")

# Browser Configuration
HEADLESS: Final[bool] = os.environ.get("HEADLESS", "true").lower() in ("true", "1", "yes")
WINDOW_WIDTH: Final[int] = int(os.environ.get("WINDOW_WIDTH", 1920))
WINDOW_HEIGHT: Final[int] = int(os.environ.get("WINDOW_HEIGHT", 1080))
PAGE_LOAD_TIMEOUT: Final[float] = float(os.environ.get("PAGE_LOAD_TIMEOUT", 30.0))
EXPLICIT_WAIT_TIMEOUT: Final[float] = float(os.environ.get("EXPLICIT_WAIT_TIMEOUT", 10.0))

# Proxy Configuration
PROXY_URL: Final[str] = os.environ.get("PROXY_URL", "").strip()
PROXY_USERNAME: Final[str] = os.environ.get("PROXY_USERNAME", "").strip()
PROXY_PASSWORD: Final[str] = os.environ.get("PROXY_PASSWORD", "").strip()

# DOM Observer Configuration
MUTATION_DEBOUNCE_MS: Final[int] = int(os.environ.get("MUTATION_DEBOUNCE_MS", 100))

# Reconnection Settings
INITIAL_RECONNECT_DELAY: Final[float] = 1.0
MAX_RECONNECT_DELAY: Final[float] = 15.0
RECONNECT_BACKOFF_FACTOR: Final[float] = 1.5
