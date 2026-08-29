"""
Unit and integration tests for BeautifulSoup HTML Normalization Pipeline.
Verifies script stripping, iframe removal, attribute sorting, structural DOM ID indexing,
whitespace preservation in <pre>/<code>, and exact non-ASCII/Unicode preservation.
"""

import pytest
from worker.dom_pipeline import HTMLNormalizer


def test_unicode_and_emoji_preservation():
    """Verify non-ASCII, Cyrillic, Chinese, and emojis are preserved exactly without double-escaping."""
    raw_html = """
    <html>
        <body>
            <h1>Привет мир 🚀</h1>
            <p>CJK: 测试 / 日本語 / 한국어</p>
            <span>Accented: résumé, naïve, über</span>
        </body>
    </html>
    """
    normalizer = HTMLNormalizer(assign_dom_ids=False)
    clean_html = normalizer.normalize(raw_html)

    assert "Привет мир 🚀" in clean_html
    assert "测试 / 日本語 / 한국어" in clean_html
    assert "résumé, naïve, über" in clean_html
    # Ensure no entity mangling
    assert "&#" not in clean_html


def test_script_and_iframe_stripping():
    """Verify scripts, noscripts, iframes, and inline event handlers are stripped."""
    raw_html = """
    <html>
        <body>
            <script type="text/javascript">
                var secret = "do_not_leak";
                document.write("<p>Injected</p>");
            </script>
            <noscript>JavaScript is required</noscript>
            <iframe src="https://tracker.adnetwork.com/pixel"></iframe>
            <button id="btn" onclick="alert('clicked')" onmouseover="track()" class="primary">Click</button>
            <a href="javascript:void(0)" class="link">Action</a>
            <!-- HTML comment to be removed -->
            <p>Legitimate content</p>
        </body>
    </html>
    """
    normalizer = HTMLNormalizer(strip_scripts=True, strip_iframes=True)
    clean_html = normalizer.normalize(raw_html)

    assert "<script" not in clean_html
    assert "do_not_leak" not in clean_html
    assert "<noscript" not in clean_html
    assert "<iframe" not in clean_html
    assert "onclick" not in clean_html
    assert "onmouseover" not in clean_html
    assert "javascript:" not in clean_html
    assert "<!--" not in clean_html
    assert "Legitimate content" in clean_html
    assert 'class="primary"' in clean_html
    assert 'id="btn"' in clean_html


def test_whitespace_normalization_and_pre_tag_preservation():
    """Verify whitespace is collapsed in standard text, but preserved in <pre>, <code>, <textarea>."""
    raw_html = """
    <html>
        <body>
            <p>
                Multiple      lines   and
                spaces     in     paragraph.
            </p>
            <pre>
def example():
    print("Exact indent preserved")
    return True
            </pre>
            <code>    indented code block    </code>
            <textarea>  preserved text in textarea  </textarea>
        </body>
    </html>
    """
    normalizer = HTMLNormalizer(assign_dom_ids=False)
    clean_html = normalizer.normalize(raw_html)

    # Paragraph whitespace is collapsed
    assert "Multiple lines and spaces in paragraph." in clean_html

    # Pre, code, textarea whitespace is strictly preserved
    assert '    print("Exact indent preserved")' in clean_html
    assert "<code>    indented code block    </code>" in clean_html
    assert "<textarea>  preserved text in textarea  </textarea>" in clean_html


def test_deterministic_attribute_sorting():
    """Verify attributes are sorted alphabetically for deterministic hashing and diffing."""
    raw_html = """
    <div z-index="10" class="card" id="card-1" aria-hidden="false" data-role="container">
        <input value="val" type="text" placeholder="Enter name" name="username" id="user-input" />
    </div>
    """
    normalizer = HTMLNormalizer(assign_dom_ids=False, sort_attributes=True)
    clean_html = normalizer.normalize(raw_html)

    # Attributes on div sorted: aria-hidden, class, data-role, id, z-index
    assert 'aria-hidden="false" class="card" data-role="container" id="card-1" z-index="10"' in clean_html
    # Attributes on input sorted: id, name, placeholder, type, value
    assert 'id="user-input" name="username" placeholder="Enter name" type="text" value="val"' in clean_html


def test_structural_dom_id_assignment():
    """Verify deterministic hierarchical data-dom-id indexing."""
    raw_html = """
    <html>
        <body>
            <div id="wrapper">
                <header><h1>Title</h1></header>
                <main><p>Content 1</p><p>Content 2</p></main>
            </div>
        </body>
    </html>
    """
    normalizer = HTMLNormalizer(assign_dom_ids=True)
    clean_html = normalizer.normalize(raw_html)

    assert 'data-dom-id="dom_0"' in clean_html  # div#wrapper
    assert 'data-dom-id="dom_0_0"' in clean_html  # header
    assert 'data-dom-id="dom_0_0_0"' in clean_html  # h1
    assert 'data-dom-id="dom_0_1"' in clean_html  # main
    assert 'data-dom-id="dom_0_1_0"' in clean_html  # p1
    assert 'data-dom-id="dom_0_1_1"' in clean_html  # p2
