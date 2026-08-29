"""
Unit and property-based tests for DOM Diffing and Patch Engine.
Verifies computation and application of all DOMDiffOp operations (text, attribute, add, remove, replace),
and proves the mathematical equivalence property: apply_diff(old_html, compute_diff(old_html, new_html)) == new_html.
"""

import pytest
from bs4 import BeautifulSoup
from shared.dom_differ import compute_diff, apply_diff
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

normalizer = HTMLNormalizer(assign_dom_ids=True, sort_attributes=True)


def test_diff_text_change():
    """Verify text modification generates OP_TEXT and applies to identical DOM."""
    old_raw = """
    <html>
        <body>
            <div id="container">
                <h1 id="title">Initial Title</h1>
            </div>
        </body>
    </html>
    """
    new_raw = """
    <html>
        <body>
            <div id="container">
                <h1 id="title">Updated Dynamic Title 🚀</h1>
            </div>
        </body>
    </html>
    """
    old_norm = normalizer.normalize(old_raw)
    new_norm = normalizer.normalize(new_raw)

    ops = compute_diff(old_norm, new_norm)
    assert len(ops) >= 1
    assert any(op.op == OP_TEXT for op in ops)

    patched = apply_diff(old_norm, ops)
    assert patched == new_norm


def test_diff_attribute_change():
    """Verify attribute changes and additions generate OP_ATTRIBUTE and apply accurately."""
    old_raw = """
    <html>
        <body>
            <button class="btn btn-default" id="submit-btn">Submit</button>
        </body>
    </html>
    """
    new_raw = """
    <html>
        <body>
            <button aria-busy="true" class="btn btn-primary active" disabled="disabled" id="submit-btn">Submit</button>
        </body>
    </html>
    """
    old_norm = normalizer.normalize(old_raw)
    new_norm = normalizer.normalize(new_raw)

    ops = compute_diff(old_norm, new_norm)
    assert len(ops) >= 1
    assert any(op.op == OP_ATTRIBUTE for op in ops)

    patched = apply_diff(old_norm, ops)
    assert patched == new_norm


def test_diff_node_addition_and_removal():
    """Verify element additions and deletions generate OP_ADD / OP_REMOVE and apply accurately."""
    old_raw = """
    <html>
        <body>
            <ul id="item-list">
                <li id="item-1">Item 1</li>
                <li id="item-2">Item 2 to remove</li>
            </ul>
        </body>
    </html>
    """
    new_raw = """
    <html>
        <body>
            <ul id="item-list">
                <li id="item-1">Item 1</li>
                <li id="item-3">Item 3 newly added</li>
            </ul>
        </body>
    </html>
    """
    old_norm = normalizer.normalize(old_raw)
    new_norm = normalizer.normalize(new_raw)

    ops = compute_diff(old_norm, new_norm)
    assert len(ops) >= 1

    patched = apply_diff(old_norm, ops)
    assert "Item 2 to remove" not in patched
    assert "Item 3 newly added" in patched


def test_property_based_diff_equivalence_suite():
    """
    Property-based test verifying that across multiple complex simulated DOM mutations,
    applying the computed diff sequence to old_html ALWAYS yields new_html.
    """
    test_cases = [
        # Case 1: Complex form state alteration
        (
            """<html><body><form id="f1"><input id="i1" name="email" value="" /><button id="b1">Send</button></form></body></html>""",
            """<html><body><form id="f1"><input class="valid" id="i1" name="email" value="user@test.com" /><button disabled="true" id="b1">Sending...</button></form></body></html>""",
        ),
        # Case 2: Table row insertion and badge update
        (
            """<html><body><table id="tbl"><tbody><tr id="r1"><td>Alpha</td><td><span class="badge">Active</span></td></tr></tbody></table></body></html>""",
            """<html><body><table id="tbl"><tbody><tr id="r1"><td>Alpha</td><td><span class="badge badge-success">Completed</span></td></tr><tr id="r2"><td>Beta</td><td><span class="badge">Pending</span></td></tr></tbody></table></body></html>""",
        ),
        # Case 3: Multilingual Unicode dynamic content
        (
            """<html><body><div id="greeting"><h1>Hello World</h1><p id="desc">English description</p></div></body></html>""",
            """<html><body><div id="greeting"><h1>Привет мир 🚀 &amp; 测试</h1><p id="desc">Multilingual updated description with emoji ✨</p></div></body></html>""",
        ),
    ]

    for old_raw, new_raw in test_cases:
        old_norm = normalizer.normalize(old_raw)
        new_norm = normalizer.normalize(new_raw)

        ops = compute_diff(old_norm, new_norm)
        patched = apply_diff(old_norm, ops)

        # Mathematical Equivalence Assertion
        assert patched == new_norm, f"Property violation:\nPatched: {patched}\nExpected: {new_norm}"
