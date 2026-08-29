"""
Zstandard Compression Tier module.
Provides transparent compression and decompression for DOM snapshots and update payloads.
Payloads exceeding COMPRESSION_THRESHOLD_BYTES are compressed via zstd (level 3) and base64 encoded.
"""

from __future__ import annotations
import os
import base64
import logging
from typing import Tuple, Union
import zstandard as zstd

logger = logging.getLogger("shared.compression")

COMPRESSION_THRESHOLD_BYTES: int = int(os.environ.get("ZSTD_THRESHOLD", 1024))  # Default 1 KB
DEFAULT_COMPRESSION_LEVEL: int = 3

_cctx = zstd.ZstdCompressor(level=DEFAULT_COMPRESSION_LEVEL)
_dctx = zstd.ZstdDecompressor()


def compress_payload(
    data: Union[str, bytes],
    threshold: int = COMPRESSION_THRESHOLD_BYTES,
    level: int = DEFAULT_COMPRESSION_LEVEL,
) -> Tuple[str, bool]:
    """
    Compress payload if size exceeds threshold.
    Returns (payload_string, is_compressed).
    If compressed, payload_string is base64-encoded zstd bytes.
    """
    if isinstance(data, str):
        raw_bytes = data.encode("utf-8")
    else:
        raw_bytes = data

    if len(raw_bytes) <= threshold:
        # Small payload: do not compress
        return (data if isinstance(data, str) else raw_bytes.decode("utf-8", errors="replace"), False)

    try:
        cctx = zstd.ZstdCompressor(level=level) if level != DEFAULT_COMPRESSION_LEVEL else _cctx
        compressed = cctx.compress(raw_bytes)
        b64_str = base64.b64encode(compressed).decode("ascii")
        reduction = (1 - (len(b64_str) / len(raw_bytes))) * 100
        logger.debug(f"Compressed {len(raw_bytes)} bytes -> {len(b64_str)} b64 bytes ({reduction:.1f}% reduction)")
        return (b64_str, True)
    except Exception as e:
        logger.warning(f"Compression failed, sending uncompressed: {e}")
        return (data if isinstance(data, str) else raw_bytes.decode("utf-8", errors="replace"), False)


def decompress_payload(data: str, is_compressed: bool) -> str:
    """
    Decompress payload if is_compressed is True.
    Returns raw UTF-8 string.
    """
    if not is_compressed or not data:
        return data

    try:
        raw_bytes = base64.b64decode(data)
        decompressed = _dctx.decompress(raw_bytes, max_output_size=50 * 1024 * 1024)
        return decompressed.decode("utf-8")
    except Exception as e:
        logger.warning(f"Decompression error: {e}. Returning raw data.")
        return data


def calculate_bandwidth_savings(original_bytes: int, compressed_bytes: int) -> float:
    """Calculate percentage of bandwidth saved (0.0 to 100.0%)."""
    if original_bytes <= 0 or compressed_bytes >= original_bytes:
        return 0.0
    return ((original_bytes - compressed_bytes) / original_bytes) * 100.0
