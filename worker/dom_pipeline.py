"""
DOM Normalization Pipeline using BeautifulSoup4.
Sanitizes raw browser HTML, strips dynamic scripts/iframes/event handlers,
assigns deterministic structural DOM IDs, sorts attributes, and preserves Unicode characters.
"""

from __future__ import annotations
import logging
import re
from typing import Optional
from bs4 import BeautifulSoup, NavigableString, Comment, Tag

logger = logging.getLogger("worker.dom_pipeline")

STRIP_TAGS = {"script", "noscript", "iframe", "embed", "object", "link"}
PRESERVE_WHITESPACE_TAGS = {"pre", "code", "textarea"}


class HTMLNormalizer:
    """
    Transforms raw browser page source into clean, deterministic, Unicode-preserved HTML.
    """
    def __init__(
        self,
        strip_scripts: bool = True,
        strip_iframes: bool = True,
        assign_dom_ids: bool = True,
        sort_attributes: bool = True,
    ) -> None:
        self.strip_scripts = strip_scripts
        self.strip_iframes = strip_iframes
        self.assign_dom_ids = assign_dom_ids
        self.sort_attributes = sort_attributes

    def normalize(self, raw_html: str) -> str:
        """
        Execute full normalization pipeline on raw HTML string.
        Returns clean, deterministic HTML with preserved Unicode characters.
        """
        if not raw_html or not raw_html.strip():
            return ""

        soup = BeautifulSoup(raw_html, "html.parser")

        # 1. Remove comments
        for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
            comment.extract()

        # 2. Remove blacklisted tags
        tags_to_remove = set()
        if self.strip_scripts:
            tags_to_remove.update({"script", "noscript"})
        if self.strip_iframes:
            tags_to_remove.update({"iframe", "embed", "object"})

        for tag_name in tags_to_remove:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        # 3. Clean attributes and remove event handlers
        for tag in soup.find_all(True):
            self._clean_tag_attributes(tag)

        # 4. Normalize text node whitespace (except pre/code/textarea)
        self._normalize_text_nodes(soup)

        # 5. Assign stable hierarchical DOM IDs if requested
        if self.assign_dom_ids and soup.body:
            self._assign_structural_dom_ids(soup.body, prefix="dom")
        elif self.assign_dom_ids and soup:
            self._assign_structural_dom_ids(soup, prefix="dom")

        # 6. Render clean Unicode HTML string without escaping non-ASCII
        # formatter=None ensures characters like '🚀' or 'Привет' remain raw Unicode
        return soup.decode(formatter=None)

    def _clean_tag_attributes(self, tag: Tag) -> None:
        """Remove inline event handlers (on*) and sort tag attributes."""
        attrs = dict(tag.attrs)
        cleaned_attrs = {}

        for k, v in attrs.items():
            k_lower = k.lower()
            # Strip inline event handlers
            if k_lower.startswith("on"):
                continue
            # Strip javascript: URLs
            if k_lower in ("href", "src", "action") and isinstance(v, str) and v.strip().lower().startswith("javascript:"):
                cleaned_attrs[k] = "#"
                continue

            cleaned_attrs[k] = v

        if self.sort_attributes:
            # Sort attributes alphabetically for deterministic comparison
            sorted_attrs = {k: cleaned_attrs[k] for k in sorted(cleaned_attrs.keys())}
            tag.attrs = sorted_attrs
        else:
            tag.attrs = cleaned_attrs

    def _normalize_text_nodes(self, soup: BeautifulSoup) -> None:
        """Normalize whitespace in text nodes outside pre/code/textarea."""
        for text_node in soup.find_all(string=True):
            if isinstance(text_node, Comment):
                continue

            # Check if inside whitespace-preserving ancestor
            if any(p.name in PRESERVE_WHITESPACE_TAGS for p in text_node.parents if isinstance(p, Tag)):
                continue

            text_val = str(text_node)
            # Collapse multiple spaces/newlines to a single space
            collapsed = re.sub(r"\s+", " ", text_val)
            if collapsed != text_val:
                text_node.replace_with(collapsed)

    def _assign_structural_dom_ids(self, element: Tag, prefix: str = "dom") -> None:
        """Assign deterministic hierarchical data-dom-id to every element."""
        counter = 0
        for child in element.find_all(True, recursive=False):
            child_id = f"{prefix}_{counter}"
            child["data-dom-id"] = child_id
            self._assign_structural_dom_ids(child, prefix=child_id)
            counter += 1
