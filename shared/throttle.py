"""
Throttle Profile system for bandwidth optimization.
Defines configurable profiles that control compression level, threshold,
payload size limits, and minimum snapshot intervals per worker.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional
import logging

logger = logging.getLogger("shared.throttle")


@dataclass(frozen=True)
class ThrottleProfile:
    """
    Immutable throttle configuration controlling bandwidth behavior per worker.
    """
    name: str
    compression_level: int          # zstd compression level (1=fastest, 9=max compression)
    compression_threshold: int      # byte count before compression kicks in
    max_snapshot_bytes: int          # hard cap on snapshot payload (0=unlimited)
    min_snapshot_interval_ms: int   # minimum ms between consecutive snapshots
    description: str                # human-readable label for UI

    def __post_init__(self) -> None:
        if not 1 <= self.compression_level <= 9:
            raise ValueError(f"compression_level must be 1-9, got {self.compression_level}")
        if self.compression_threshold < 0:
            raise ValueError(f"compression_threshold must be >= 0, got {self.compression_threshold}")
        if self.max_snapshot_bytes < 0:
            raise ValueError(f"max_snapshot_bytes must be >= 0, got {self.max_snapshot_bytes}")
        if self.min_snapshot_interval_ms < 0:
            raise ValueError(f"min_snapshot_interval_ms must be >= 0, got {self.min_snapshot_interval_ms}")


# Built-in profiles
PROFILE_REALTIME = ThrottleProfile(
    name="realtime",
    compression_level=1,
    compression_threshold=512,
    max_snapshot_bytes=0,
    min_snapshot_interval_ms=0,
    description="Realtime — fastest compression, no rate limiting",
)

PROFILE_BALANCED = ThrottleProfile(
    name="balanced",
    compression_level=3,
    compression_threshold=1024,
    max_snapshot_bytes=5 * 1024 * 1024,
    min_snapshot_interval_ms=500,
    description="Balanced — moderate compression, 500ms minimum interval",
)

PROFILE_LOW_BANDWIDTH = ThrottleProfile(
    name="low_bandwidth",
    compression_level=9,
    compression_threshold=256,
    max_snapshot_bytes=2 * 1024 * 1024,
    min_snapshot_interval_ms=2000,
    description="Low Bandwidth — maximum compression, 2s minimum interval",
)

# Profile registry keyed by name
THROTTLE_PROFILES: Dict[str, ThrottleProfile] = {
    PROFILE_REALTIME.name: PROFILE_REALTIME,
    PROFILE_BALANCED.name: PROFILE_BALANCED,
    PROFILE_LOW_BANDWIDTH.name: PROFILE_LOW_BANDWIDTH,
}

DEFAULT_PROFILE_NAME: str = "balanced"


def get_profile(name: str) -> Optional[ThrottleProfile]:
    """Look up a throttle profile by name. Returns None if not found."""
    return THROTTLE_PROFILES.get(name)


def get_default_profile() -> ThrottleProfile:
    """Return the default throttle profile."""
    return THROTTLE_PROFILES[DEFAULT_PROFILE_NAME]


def get_profile_names() -> list[str]:
    """Return all available profile names in display order."""
    return [PROFILE_REALTIME.name, PROFILE_BALANCED.name, PROFILE_LOW_BANDWIDTH.name]
