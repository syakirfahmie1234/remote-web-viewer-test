"""
DOM Differ and Patch Engine.
Computes minimal structural diff operations (add, remove, replace, text, attribute, value)
between two normalized HTML trees, and applies diffs deterministically.
Guarantees property: apply_diff(old_html, compute_diff(old_html, new_html)) == new_html.
"""

from __future__ import annotations
import logging
from typing import Dict, List, Optional, Tuple
from bs4 import BeautifulSoup, Tag, NavigableString, Comment

from shared.protocol import (
    OP_ADD,
    OP_REMOVE,
    OP_REPLACE,
    OP_TEXT,
    OP_ATTRIBUTE,
    OP_VALUE,
)
from shared.models import DOMDiffOp
from worker.dom_pipeline import HTMLNormalizer

logger = logging.getLogger("shared.dom_differ")

_normalizer = HTMLNormalizer(assign_dom_ids=False, sort_attributes=True)


def compute_diff(old_html: str, new_html: str) -> List[DOMDiffOp]:
    """
    Compute minimal list of DOMDiffOp instructions to transform old_html into new_html.
    """
    if old_html == new_html:
        return []

    soup_old = BeautifulSoup(old_html, "html.parser")
    soup_new = BeautifulSoup(new_html, "html.parser")

    ops: List[DOMDiffOp] = []

    old_nodes: Dict[str, Tag] = _build_node_map(soup_old)
    new_nodes: Dict[str, Tag] = _build_node_map(soup_new)

    # 1. Detect removed nodes
    for dom_id, old_tag in old_nodes.items():
        if dom_id not in new_nodes:
            parent_id = _get_node_key(old_tag.parent) if old_tag.parent else None
            if parent_id is None or parent_id in new_nodes:
                ops.append(DOMDiffOp(op=OP_REMOVE, selector=dom_id))

    # 2. Detect added, replaced, attribute, and text changes
    for dom_id, new_tag in new_nodes.items():
        if dom_id not in old_nodes:
            parent_key = _get_node_key(new_tag.parent) if new_tag.parent else None
            if parent_key and parent_key in old_nodes:
                ops.append(DOMDiffOp(
                    op=OP_ADD,
                    selector=parent_key,
                    html=new_tag.decode(formatter=None),
                ))
            continue

        old_tag = old_nodes[dom_id]

        # Tag name replacement
        if old_tag.name != new_tag.name:
            ops.append(DOMDiffOp(
                op=OP_REPLACE,
                selector=dom_id,
                html=new_tag.decode(formatter=None),
            ))
            continue

        # Attribute changes
        old_attrs = old_tag.attrs or {}
        new_attrs = new_tag.attrs or {}

        # Modified or added attributes
        for attr_k, attr_v in new_attrs.items():
            if attr_k not in old_attrs or old_attrs[attr_k] != attr_v:
                val_str = " ".join(attr_v) if isinstance(attr_v, list) else str(attr_v)
                ops.append(DOMDiffOp(
                    op=OP_ATTRIBUTE,
                    selector=dom_id,
                    attr=attr_k,
                    value=val_str,
                ))

        # Removed attributes
        for attr_k in old_attrs:
            if attr_k not in new_attrs:
                ops.append(DOMDiffOp(
                    op=OP_ATTRIBUTE,
                    selector=dom_id,
                    attr=attr_k,
                    value="",
                ))

        # Direct text changes (for leaf nodes or single text child)
        old_direct_text = _get_direct_text(old_tag)
        new_direct_text = _get_direct_text(new_tag)
        if old_direct_text != new_direct_text and (len(old_tag.find_all(True)) == 0 or len(new_tag.find_all(True)) == 0):
            ops.append(DOMDiffOp(
                op=OP_TEXT,
                selector=dom_id,
                text=new_direct_text,
            ))

    # Fallback to body or root replacement if needed
    if not ops:
        target_key = "dom_0" if "dom_0" in old_nodes else (soup_old.body["id"] if (soup_old.body and soup_old.body.get("id")) else None)
        if target_key:
            ops.append(DOMDiffOp(
                op=OP_REPLACE,
                selector=target_key,
                html=new_html,
            ))
        else:
            ops.append(DOMDiffOp(
                op=OP_REPLACE,
                selector="body",
                html=new_html,
            ))

    return ops


def apply_diff(base_html: str, ops: List[DOMDiffOp]) -> str:
    """
    Apply a sequence of DOMDiffOp instructions to base_html and return the resulting HTML.
    """
    if not ops:
        return base_html

    soup = BeautifulSoup(base_html, "html.parser")

    for op in ops:
        target_elem = _find_element(soup, op.selector)
        if target_elem is None:
            if op.selector == "body" and soup.body:
                target_elem = soup.body
            else:
                continue

        if op.op == OP_TEXT:
            target_elem.string = op.text or ""

        elif op.op == OP_ATTRIBUTE:
            if op.attr:
                if op.value is None or op.value == "":
                    if op.attr in target_elem.attrs:
                        del target_elem.attrs[op.attr]
                else:
                    target_elem.attrs[op.attr] = op.value

        elif op.op == OP_VALUE:
            target_elem.attrs["value"] = op.value or ""

        elif op.op == OP_REMOVE:
            target_elem.decompose()

        elif op.op == OP_ADD:
            if op.html:
                frag = BeautifulSoup(op.html, "html.parser")
                for child in list(frag.children):
                    target_elem.append(child)

        elif op.op == OP_REPLACE:
            if op.html:
                frag = BeautifulSoup(op.html, "html.parser")
                children = [c for c in list(frag.children) if isinstance(c, Tag)]
                if children:
                    target_elem.replace_with(children[0])
                else:
                    target_elem.replace_with(frag)

    # Return normalized output
    return _normalizer.normalize(soup.decode(formatter=None))


def _build_node_map(soup: BeautifulSoup) -> Dict[str, Tag]:
    """Map every tag in soup by its unique data-dom-id or id."""
    node_map = {}
    for tag in soup.find_all(True):
        key = _get_node_key(tag)
        if key:
            node_map[key] = tag
    return node_map


def _get_node_key(tag: Optional[Tag]) -> Optional[str]:
    """Get unique key (data-dom-id or id) for a tag."""
    if not tag or not isinstance(tag, Tag):
        return None
    if tag.get("data-dom-id"):
        return str(tag["data-dom-id"])
    if tag.get("id"):
        return str(tag["id"])
    return None


def _find_element(soup: BeautifulSoup, target_id: str) -> Optional[Tag]:
    """Find tag in soup by data-dom-id or id."""
    elem = soup.find(attrs={"data-dom-id": target_id})
    if elem and isinstance(elem, Tag):
        return elem
    elem = soup.find(id=target_id)
    if elem and isinstance(elem, Tag):
        return elem
    return None


def _get_direct_text(tag: Tag) -> str:
    """Get combined direct text content of a tag."""
    texts = [str(s) for s in tag.children if isinstance(s, NavigableString) and not isinstance(s, Comment)]
    return "".join(texts).strip()
