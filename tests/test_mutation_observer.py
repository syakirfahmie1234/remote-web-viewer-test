"""
Unit and integration tests for Worker MutationObserver subsystem.
Verifies observer injection, DOM mutation capture across attribute/childList/characterData events,
and safe draining across page navigations and reloads.
"""

import os
import pytest
from worker.browser import BrowserManager
from worker.mutation_observer import DOMMutationTracker

os.environ["HEADLESS"] = "true"


@pytest.fixture(scope="module")
def browser_instance():
    """Persistent headless Chrome browser for testing MutationObserver."""
    browser = BrowserManager()
    browser.start()
    yield browser
    browser.quit()


@pytest.fixture
def mutation_tracker(browser_instance):
    """Clean DOMMutationTracker instance with a loaded HTML test page."""
    html_page = """
    <!DOCTYPE html>
    <html>
    <head><title>Mutation Test</title></head>
    <body>
        <h1 id="heading">Original Title</h1>
        <div id="container">
            <p id="para-1">Initial Paragraph</p>
        </div>
        <button id="test-btn" class="default-btn">Click</button>
    </body>
    </html>
    """
    browser_instance.driver.get(f"data:text/html;charset=utf-8,{html_page}")
    tracker = DOMMutationTracker(browser=browser_instance, worker_id="worker-test-mo")
    tracker.inject()
    return tracker


def test_observer_injection_state(browser_instance):
    """Verify is_injected() accurately reflects observer state in page context."""
    tracker = DOMMutationTracker(browser=browser_instance, worker_id="worker-test-mo")
    # Fresh page before injection
    browser_instance.driver.get("data:text/html;charset=utf-8,<html><body><h1>Fresh</h1></body></html>")
    assert tracker.is_injected() is False

    success = tracker.inject()
    assert success is True
    assert tracker.is_injected() is True


def test_capture_attribute_and_child_mutations(mutation_tracker, browser_instance):
    """Verify attribute changes and element additions/removals are captured and drained."""
    # 1. Trigger attribute mutation
    browser_instance.execute_script(
        "document.getElementById('test-btn').setAttribute('class', 'updated-btn-class');"
    )

    # 2. Trigger childList (addition) mutation
    browser_instance.execute_script(
        """
        const newDiv = document.createElement('div');
        newDiv.id = 'dynamic-card';
        newDiv.innerText = 'New Card';
        document.getElementById('container').appendChild(newDiv);
        """
    )

    # 3. Trigger childList (removal) mutation
    browser_instance.execute_script(
        "document.getElementById('para-1').remove();"
    )

    # Check pending
    assert mutation_tracker.has_pending_mutations() is True

    # Drain mutations
    mutations = mutation_tracker.drain_mutations()
    assert len(mutations) >= 3

    # Check attribute mutation record
    attr_muts = [m for m in mutations if m["type"] == "attributes"]
    assert len(attr_muts) >= 1
    assert attr_muts[0]["targetId"] == "test-btn"
    assert attr_muts[0]["attributeName"] == "class"

    # Check childList addition record
    add_muts = [m for m in mutations if m["type"] == "childList" and m["addedCount"] > 0]
    assert len(add_muts) >= 1
    assert add_muts[0]["targetId"] == "container"

    # Check childList removal record
    rem_muts = [m for m in mutations if m["type"] == "childList" and m["removedCount"] > 0]
    assert len(rem_muts) >= 1

    # Queue must now be empty
    assert mutation_tracker.has_pending_mutations() is False
    assert len(mutation_tracker.drain_mutations()) == 0


def test_capture_text_character_data_mutations(mutation_tracker, browser_instance):
    """Verify text node content changes are captured."""
    browser_instance.execute_script(
        "document.getElementById('heading').firstChild.nodeValue = 'Modified Title Text';"
    )

    mutations = mutation_tracker.drain_mutations()
    assert len(mutations) >= 1
    char_muts = [m for m in mutations if m["type"] == "characterData"]
    assert len(char_muts) >= 1


def test_safe_draining_across_page_navigation(mutation_tracker, browser_instance):
    """
    Verify observer survives page navigations without crashing,
    and automatically re-injects and tracks new page mutations.
    """
    # 1. Page 1 mutations
    browser_instance.execute_script("document.body.setAttribute('data-theme', 'dark');")
    muts1 = mutation_tracker.drain_mutations()
    assert len(muts1) >= 1

    # 2. Navigate to Page 2 (wipes old JS window context)
    page_2_html = "<html><body><h2 id='p2-heading'>Page 2 Content</h2></body></html>"
    browser_instance.driver.get(f"data:text/html;charset=utf-8,{page_2_html}")

    # Draining right after navigation safely re-injects and returns without error
    muts_nav = mutation_tracker.drain_mutations()
    assert isinstance(muts_nav, list)

    # 3. Trigger new mutations on Page 2
    mutation_tracker.ensure_injected()
    browser_instance.execute_script(
        "document.getElementById('p2-heading').setAttribute('data-state', 'loaded');"
    )

    muts2 = mutation_tracker.drain_mutations()
    assert len(muts2) >= 1
    assert muts2[0]["targetId"] == "p2-heading"
    assert muts2[0]["attributeName"] == "data-state"
