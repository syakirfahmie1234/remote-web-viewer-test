"""
MutationObserver module for Worker.
Injects a lightweight JavaScript MutationObserver into the active Chrome session,
captures DOM mutations in real-time, and drains them atomically across page lifecycles and navigations.
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional
from selenium.common.exceptions import WebDriverException, JavascriptException

from worker.browser import BrowserManager
from worker.config import WORKER_ID

logger = logging.getLogger(f"worker.mutation_observer.{WORKER_ID}")

INJECT_OBSERVER_JS = """
return (function() {
    if (window.__remoteDomObserverInstalled) {
        return true;
    }
    window.__remoteDomObserverInstalled = true;
    window.__remoteDomMutations = [];
    window.__remoteDomMutationSeq = 0;

    const observer = new MutationObserver(function(mutationsList) {
        for (let mutation of mutationsList) {
            let target = mutation.target;
            let record = {
                type: mutation.type,
                targetId: (target && target.id) ? target.id : null,
                targetTag: (target && target.tagName) ? target.tagName.toLowerCase() : null,
                targetDomId: (target && target.getAttribute) ? target.getAttribute('data-dom-id') : null,
                attributeName: mutation.attributeName || null,
                oldValue: mutation.oldValue || null,
                addedCount: mutation.addedNodes ? mutation.addedNodes.length : 0,
                removedCount: mutation.removedNodes ? mutation.removedNodes.length : 0,
                timestamp: Date.now(),
                seq: window.__remoteDomMutationSeq++
            };
            window.__remoteDomMutations.push(record);
            if (window.__remoteDomMutations.length > 5000) {
                window.__remoteDomMutations.shift();
            }
        }
    });

    const targetNode = document.documentElement || document.body;
    if (targetNode) {
        observer.observe(targetNode, {
            childList: true,
            attributes: true,
            characterData: true,
            subtree: true,
            attributeOldValue: true,
            characterDataOldValue: true
        });
        window.__remoteDomObserver = observer;
        return true;
    }
    return false;
})();
"""

DRAIN_MUTATIONS_JS = """
return (function() {
    if (!window.__remoteDomMutations || window.__remoteDomMutations.length === 0) {
        return [];
    }
    const drained = window.__remoteDomMutations;
    window.__remoteDomMutations = [];
    return drained;
})();
"""

CHECK_INJECTED_JS = "return Boolean(window.__remoteDomObserverInstalled);"


class DOMMutationTracker:
    """
    Manages JavaScript MutationObserver lifecycle and mutation polling in persistent Chrome.
    """
    def __init__(self, browser: BrowserManager, worker_id: str = WORKER_ID) -> None:
        self.browser = browser
        self.worker_id = worker_id

    def is_injected(self) -> bool:
        """Check if MutationObserver is installed in the current browser page context."""
        if not self.browser.is_alive():
            return False
        try:
            return bool(self.browser.execute_script(CHECK_INJECTED_JS))
        except (WebDriverException, JavascriptException):
            return False
        except Exception:
            return False

    def inject(self) -> bool:
        """Inject MutationObserver into current page context."""
        if not self.browser.is_alive():
            return False
        try:
            result = self.browser.execute_script(INJECT_OBSERVER_JS)
            logger.info(f"Injected MutationObserver on '{self.worker_id}' (success={result})")
            return bool(result)
        except Exception as e:
            from selenium.common.exceptions import UnexpectedAlertPresentException
            if isinstance(e, UnexpectedAlertPresentException):
                raise
            logger.warning(f"Failed to inject MutationObserver on '{self.worker_id}': {e}")
            return False

    def ensure_injected(self) -> bool:
        """Ensure MutationObserver is active, re-injecting if page was navigated or reloaded."""
        if not self.is_injected():
            return self.inject()
        return True

    def drain_mutations(self) -> List[Dict[str, Any]]:
        """
        Atomically retrieve and flush pending DOM mutations from the browser context.
        Safely survives page navigations and context resets.
        """
        if not self.browser.is_alive():
            return []

        try:
            # If not injected (e.g. after navigation), re-inject
            if not self.is_injected():
                self.inject()
                return []

            mutations = self.browser.execute_script(DRAIN_MUTATIONS_JS)
            if mutations and len(mutations) > 0:
                logger.debug(f"Drained {len(mutations)} mutations on '{self.worker_id}'")
            return mutations or []
        except (WebDriverException, JavascriptException) as e:
            logger.debug(f"Mutation drain interrupted (likely page navigating): {e}")
            # Re-inject on next turn
            return []
        except Exception as e:
            logger.warning(f"Unexpected error while draining mutations on '{self.worker_id}': {e}")
            return []

    def has_pending_mutations(self) -> bool:
        """Check if any mutations are currently queued in browser without draining."""
        if not self.browser.is_alive():
            return False
        try:
            count = self.browser.execute_script(
                "return (window.__remoteDomMutations) ? window.__remoteDomMutations.length : 0;"
            )
            return bool(count and count > 0)
        except Exception:
            return False
