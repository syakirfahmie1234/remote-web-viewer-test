import asyncio
import logging
from typing import Any, Dict, Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
    InvalidSelectorException,
    StaleElementReferenceException,
)

from shared.models import CommandMessage, CommandResultMessage
from shared.messages import create_command_result
from worker.browser import BrowserManager
from worker.config import WORKER_ID, EXPLICIT_WAIT_TIMEOUT

logger = logging.getLogger(__name__)

# Allowed commands to prevent arbitrary execution
COMMAND_ALLOWLIST = {
    "navigate",
    "click",
    "type",
    "clear",
    "keypress",
    "scroll",
    "highlight",
    "page_source",
    "back",
    "forward",
    "refresh",
    "screenshot",
}

CMD_NAVIGATE = "navigate"
CMD_CLICK = "click"
CMD_TYPE = "type"
CMD_CLEAR = "clear"
CMD_KEYPRESS = "keypress"
CMD_SCROLL = "scroll"
CMD_HIGHLIGHT = "highlight"
CMD_PAGE_SOURCE = "page_source"
CMD_BACK = "back"
CMD_FORWARD = "forward"
CMD_REFRESH = "refresh"
CMD_SCREENSHOT = "screenshot"

KEY_MAP = {
    "enter": "\ue007",
    "tab": "\ue004",
    "escape": "\ue00c",
    "backspace": "\ue003",
    "arrow_up": "\ue013",
    "arrow_down": "\ue015",
    "arrow_left": "\ue012",
    "arrow_right": "\ue014",
}

class CommandHandler:
    """
    Executes commands on the BrowserManager instance using explicit waits.
    Sequential execution is enforced by an async lock.
    """
    def __init__(
        self,
        browser: BrowserManager,
        worker_id: str = WORKER_ID,
        default_timeout: float = EXPLICIT_WAIT_TIMEOUT,
    ) -> None:
        self.browser = browser
        self.worker_id = worker_id
        self.default_timeout = default_timeout

    async def execute(self, msg: CommandMessage) -> CommandResultMessage:
        """
        Execute a received CommandMessage sequentially.
        Catches all Selenium exceptions and returns a structured CommandResultMessage.
        """
        return await asyncio.to_thread(self._execute_sync, msg)

    def _execute_sync(self, msg: CommandMessage) -> CommandResultMessage:
        command = msg.command
        payload = msg.payload or {}

        if command not in COMMAND_ALLOWLIST:
            logger.warning(f"Rejected non-allowlisted command: {command}")
            return create_command_result(
                worker_id=self.worker_id,
                command=command,
                success=False,
                error=f"Command '{command}' is not in the allowlist",
            )

        try:
            result_payload = self._dispatch_command(command, payload)
            logger.info(f"Command '{command}' executed successfully on worker '{self.worker_id}'")
            return create_command_result(
                worker_id=self.worker_id,
                command=command,
                success=True,
                payload=result_payload,
            )
        except NoSuchElementException as e:
            err_msg = f"Element not found: {e.msg or str(e)}"
            logger.warning(f"Command '{command}' failed on '{self.worker_id}': {err_msg}")
            return create_command_result(
                worker_id=self.worker_id,
                command=command,
                success=False,
                error=err_msg,
            )
        except TimeoutException as e:
            err_msg = f"Timed out waiting for element: {e.msg or str(e)}"
            logger.warning(f"Command '{command}' timed out on '{self.worker_id}': {err_msg}")
            return create_command_result(
                worker_id=self.worker_id,
                command=command,
                success=False,
                error=err_msg,
            )
        except (InvalidSelectorException, ValueError) as e:
            err_msg = f"Invalid selector or argument: {str(e)}"
            logger.warning(f"Command '{command}' rejected on '{self.worker_id}': {err_msg}")
            return create_command_result(
                worker_id=self.worker_id,
                command=command,
                success=False,
                error=err_msg,
            )
        except StaleElementReferenceException as e:
            err_msg = f"Stale element reference: {str(e)}"
            logger.warning(f"Command '{command}' stale element on '{self.worker_id}': {err_msg}")
            return create_command_result(
                worker_id=self.worker_id,
                command=command,
                success=False,
                error=err_msg,
            )
        except WebDriverException as e:
            err_msg = f"Browser interaction error: {e.msg or str(e)}"
            logger.error(f"Command '{command}' webdriver error on '{self.worker_id}': {err_msg}")
            return create_command_result(
                worker_id=self.worker_id,
                command=command,
                success=False,
                error=err_msg,
            )
        except Exception as e:
            err_msg = f"Unexpected command execution error: {str(e)}"
            logger.error(f"Command '{command}' unexpected error on '{self.worker_id}': {err_msg}")
            return create_command_result(
                worker_id=self.worker_id,
                command=command,
                success=False,
                error=err_msg,
            )

    def _dispatch_command(self, command: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Synchronous dispatcher running inside worker thread."""
        driver = self.browser.driver
        if not self.browser.is_alive() or driver is None:
            raise WebDriverException("Browser session is not running or crashed")

        if command == CMD_NAVIGATE:
            url = payload.get("url")
            if not url:
                raise ValueError("Navigate command requires 'url' in payload")
            self.browser.navigate(url)
            return {"url": self.browser.get_current_url(), "title": self.browser.get_title()}

        elif command == CMD_PAGE_SOURCE:
            return {"page_source": self.browser.get_page_source()}

        elif command == CMD_CLICK:
            selector = payload.get("selector")
            if not selector:
                raise ValueError("Click command requires 'selector' in payload")
            self._highlight_element(selector, duration_ms=600)
            element = self._wait_for_element(selector, condition="clickable")
            element.click()
            return {"clicked": selector}

        elif command == CMD_TYPE:
            selector = payload.get("selector")
            text = payload.get("text", "")
            clear_first = payload.get("clear_first", False)
            if not selector:
                raise ValueError("Type command requires 'selector' in payload")
            self._highlight_element(selector, duration_ms=400)
            element = self._wait_for_element(selector, condition="visible")
            if clear_first:
                element.clear()
            element.send_keys(text)
            return {"typed": text, "selector": selector}

        elif command == CMD_CLEAR:
            selector = payload.get("selector")
            if not selector:
                raise ValueError("Clear command requires 'selector' in payload")
            self._highlight_element(selector, duration_ms=400)
            element = self._wait_for_element(selector, condition="visible")
            element.clear()
            return {"cleared": selector}

        elif command == CMD_HIGHLIGHT:
            selector = payload.get("selector")
            if not selector:
                raise ValueError("Highlight command requires 'selector' in payload")
            duration_ms = int(payload.get("duration_ms", 1500))
            color = payload.get("color", "#FF6B00")
            count = self._highlight_element(selector, duration_ms=duration_ms, color=color)
            return {"highlighted": selector, "elements_found": count}

        elif command == CMD_KEYPRESS:
            selector = payload.get("selector")
            key_name = payload.get("key", "").lower().strip()
            if not key_name:
                raise ValueError("Keypress command requires 'key' in payload")

            selenium_key = KEY_MAP.get(key_name, key_name)
            if selector:
                element = self._wait_for_element(selector, condition="visible")
                element.send_keys(selenium_key)
            else:
                # Send key to active element or body
                active = driver.switch_to.active_element
                active.send_keys(selenium_key)
            return {"key": key_name}

        elif command == CMD_SCROLL:
            x = int(payload.get("x", 0))
            y = int(payload.get("y", 0))
            selector = payload.get("selector")

            if selector:
                element = self._wait_for_element(selector, condition="present")
                driver.execute_script("arguments[0].scrollBy(arguments[1], arguments[2]);", element, x, y)
            else:
                driver.execute_script("window.scrollBy(arguments[0], arguments[1]);", x, y)
            return {"scrolled_x": x, "scrolled_y": y}

        elif command == CMD_BACK:
            driver.back()
            return {"url": self.browser.get_current_url()}

        elif command == CMD_FORWARD:
            driver.forward()
            return {"url": self.browser.get_current_url()}

        elif command == CMD_REFRESH:
            driver.refresh()
            return {"url": self.browser.get_current_url()}

        elif command == CMD_SCREENSHOT:
            b64_data = self.browser.take_screenshot_base64()
            return {"screenshot_base64": b64_data}

        else:
            raise ValueError(f"Unknown command '{command}'")

    def _wait_for_element(
        self,
        selector: str,
        by: By = By.CSS_SELECTOR,
        timeout: Optional[float] = None,
        condition: str = "clickable",
    ) -> Any:
        """
        Explicit wait helper.
        Waits for element condition without using bare time.sleep().
        """
        driver = self.browser.driver
        t = timeout or self.default_timeout
        wait = WebDriverWait(driver, t)

        locator = (by, selector)

        if condition == "clickable":
            return wait.until(EC.element_to_be_clickable(locator))
        elif condition == "visible":
            return wait.until(EC.visibility_of_element_located(locator))
        elif condition == "present":
            return wait.until(EC.presence_of_element_located(locator))
        else:
            return wait.until(EC.presence_of_element_located(locator))

    def _highlight_element(
        self,
        selector: str,
        duration_ms: int = 800,
        color: str = "#FF6B00",
    ) -> int:
        """
        Briefly inject a visible CSS outline highlight on matching DOM elements.
        Uses a self-cleaning setTimeout JS injection - no permanent DOM mutation.
        Returns the count of elements that matched the selector.
        Silently skips if no elements match or browser session is unavailable.
        """
        driver = self.browser.driver
        if not driver:
            return 0
        try:
            # Inline JS: apply outline, schedule auto-removal after duration_ms
            script = """
(function() {
    var sel = arguments[0];
    var dur = arguments[1];
    var col = arguments[2];
    var elems;
    try {
        elems = document.querySelectorAll(sel);
    } catch(e) { return 0; }
    var saved = [];
    for (var i = 0; i < elems.length; i++) {
        saved.push({
            el: elems[i],
            outline: elems[i].style.outline,
            boxShadow: elems[i].style.boxShadow,
            transition: elems[i].style.transition
        });
        elems[i].style.outline = '3px solid ' + col;
        elems[i].style.boxShadow = '0 0 0 5px ' + col + '44';
        elems[i].style.transition = 'outline 0.1s ease, box-shadow 0.1s ease';
    }
    setTimeout(function() {
        for (var j = 0; j < saved.length; j++) {
            saved[j].el.style.outline = saved[j].outline;
            saved[j].el.style.boxShadow = saved[j].boxShadow;
            saved[j].el.style.transition = saved[j].transition;
        }
    }, dur);
    return elems.length;
})(arguments[0], arguments[1], arguments[2]);
"""
            count = driver.execute_script(script, selector, duration_ms, color)
            return int(count or 0)
        except Exception as e:
            logger.debug(f"_highlight_element silently ignored error: {e}")
            return 0
