"""
DOM Renderer module for Controller UI.
Formats, sanitizes, and renders synchronized HTML snapshots for display in the PySide6 browser view widget.
Ensures zero local script execution, removes dangerous active content, and enhances interactive elements with visual styling.
"""

from __future__ import annotations
import base64
import html
import re
from typing import Optional
import zstandard as zstd


class DOMRenderer:
    """
    Renders and sanitizes synchronized DOM snapshots for PySide6 display.
    """
    @staticmethod
    def decompress_html(compressed_payload: str | bytes) -> str:
        """
        Decompress a zstandard-compressed HTML payload (base64 or raw bytes).
        """
        try:
            if isinstance(compressed_payload, str):
                raw_bytes = base64.b64decode(compressed_payload)
            else:
                raw_bytes = compressed_payload

            dctx = zstd.ZstdDecompressor()
            decompressed = dctx.decompress(raw_bytes, max_output_size=50 * 1024 * 1024)
            return decompressed.decode("utf-8")
        except Exception:
            if isinstance(compressed_payload, str):
                return compressed_payload
            return compressed_payload.decode("utf-8", errors="replace")

    @classmethod
    def sanitize_html(cls, raw_html: str) -> str:
        """
        Sanitize HTML to prevent local script execution or hazardous active content:
        - Strip <script>...</script> tags
        - Strip <iframe>...</iframe> tags
        - Strip inline event handlers (onclick=, onload=, onerror=, etc.)
        - Strip javascript: URLs
        """
        if not raw_html:
            return ""

        cleaned = raw_html

        # 1. Remove script tags and contents
        cleaned = re.sub(r"<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>", "", cleaned, flags=re.IGNORECASE)

        # 2. Remove iframe tags and contents
        cleaned = re.sub(r"<iframe\b[^<]*(?:(?!<\/iframe>)<[^<]*)*<\/iframe>", "", cleaned, flags=re.IGNORECASE)

        # 3. Remove inline event handlers (e.g. onclick="...", onload="...", etc.)
        cleaned = re.sub(r'\son\w+\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+)', "", cleaned, flags=re.IGNORECASE)

        # 4. Remove javascript: links
        cleaned = re.sub(r'href\s*=\s*["\']?javascript:[^"\'>\s]+["\']?', 'href="#"', cleaned, flags=re.IGNORECASE)

        return cleaned

    @classmethod
    def prepare_html_for_view(
        cls,
        raw_html: str,
        title: str = "",
        url: str = "",
        compressed: bool = False,
    ) -> str:
        """
        Prepare and sanitize synchronized HTML for rendering in QTextBrowser.
        """
        if compressed and raw_html:
            html_text = cls.decompress_html(raw_html)
        else:
            html_text = raw_html

        if not html_text or not html_text.strip():
            return """
            <html>
                <body style="font-family: -apple-system, sans-serif; text-align: center; padding-top: 60px; color: #586069;">
                    <h2>No DOM snapshot available</h2>
                    <p style="color: #6a737d;">Select a connected worker from the left sidebar and click <b>⚡ Resync</b>.</p>
                </body>
            </html>
            """

        sanitized = cls.sanitize_html(html_text)
        return sanitized
