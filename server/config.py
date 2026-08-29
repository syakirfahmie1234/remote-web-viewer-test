"""
Server configuration loading from environment variables.
Handles PORT (Render deployment), authentication tokens, target domain, and compression.
"""

import os
from typing import Final
from dotenv import load_dotenv

load_dotenv()


# Port for Render or local execution
PORT: Final[int] = int(os.environ.get("PORT", 8000))

# Authentication tokens - strictly loaded from environment
WORKER_TOKEN: Final[str] = os.environ.get("WORKER_TOKEN", "default-worker-token-secret")
CONTROLLER_TOKEN: Final[str] = os.environ.get("CONTROLLER_TOKEN", "default-controller-token-secret")

# Target Domain authorization restriction
TARGET_DOMAIN: Final[str] = os.environ.get("TARGET_DOMAIN", "https://example.com")

# Default Zstandard compression level
ZSTD_LEVEL: Final[int] = int(os.environ.get("ZSTD_LEVEL", 3))

# Session Management
SESSION_TIMEOUT_SECONDS: Final[int] = int(os.environ.get("SESSION_TIMEOUT_SECONDS", 3600))
# Format: "token:w1,w2;token2:w3"
CONTROLLER_ALLOWED_WORKERS: Final[str] = os.environ.get("CONTROLLER_ALLOWED_WORKERS", "")
