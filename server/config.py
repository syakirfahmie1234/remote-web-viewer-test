import os
from typing import Final
from dotenv import load_dotenv

load_dotenv()

PORT: Final[int] = int(os.environ.get("PORT", 8000))
WORKER_TOKEN: Final[str] = os.environ.get("WORKER_TOKEN", "test-worker-123")
CONTROLLER_TOKEN: Final[str] = os.environ.get("CONTROLLER_TOKEN", "test-controller-456")
TARGET_DOMAIN: Final[str] = os.environ.get("TARGET_DOMAIN", "https://example.com")
ZSTD_LEVEL: Final[int] = int(os.environ.get("ZSTD_LEVEL", 3))
SESSION_TIMEOUT_SECONDS: Final[int] = int(os.environ.get("SESSION_TIMEOUT_SECONDS", 3600))
CONTROLLER_ALLOWED_WORKERS: Final[str] = os.environ.get("CONTROLLER_ALLOWED_WORKERS", "")
