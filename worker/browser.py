"""
Browser manager module.
Manages a single persistent Chrome WebDriver session per Worker instance.
Provides robust crash detection, Unicode-safe page source acquisition, and explicit waits.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    WebDriverException,
    InvalidSessionIdException,
    NoSuchWindowException,
)

from worker.config import (
    HEADLESS,
    WINDOW_WIDTH,
    WINDOW_HEIGHT,
    PAGE_LOAD_TIMEOUT,
    TARGET_DOMAIN,
    WORKER_ID,
    PROXY_URL,
    PROXY_USERNAME,
    PROXY_PASSWORD,
)

logger = logging.getLogger(f"worker.browser.{WORKER_ID}")

@dataclass
class BrowserConfig:
    headless: bool = True
    proxy_url: Optional[str] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None


class BrowserManager:
    """
    Manages the lifecycle and direct interaction with the persistent Chrome browser.
    """
    def __init__(self, user_data_dir: Optional[str] = None) -> None:
        self.driver: Optional[webdriver.Chrome] = None
        self.user_data_dir = user_data_dir
        self._target_netloc = urlparse(TARGET_DOMAIN).netloc.lower()
        self._current_config = BrowserConfig(
            headless=HEADLESS,
            proxy_url=PROXY_URL if PROXY_URL else None,
            proxy_username=PROXY_USERNAME if PROXY_USERNAME else None,
            proxy_password=PROXY_PASSWORD if PROXY_PASSWORD else None,
        )

    def start(self) -> None:
        """
        Start a new Chrome browser session with optimal options.
        """
        if self.driver and self.is_alive():
            logger.info("Chrome session already running and alive")
            return

        conf = self._current_config
        proxy_log = f"proxy={conf.proxy_url}" if conf.proxy_url else "proxy=None"
        auth_log = "auth=yes" if conf.proxy_username else "auth=no"
        logger.info(f"Starting Chrome browser (headless={conf.headless}, profile={self.user_data_dir}, {proxy_log}, {auth_log})...")
        
        options = Options()

        if conf.headless:
            options.add_argument("--headless=new")

        if self.user_data_dir:
            options.add_argument(f"--user-data-dir={self.user_data_dir}")

        if conf.proxy_url:
            options.add_argument(f"--proxy-server={conf.proxy_url}")
            
        options.add_argument(f"--window-size={WINDOW_WIDTH},{WINDOW_HEIGHT}")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-infobars")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--log-level=3")  # Suppress internal chrome noise

        try:
            if conf.proxy_username and conf.proxy_password and conf.proxy_url:
                import tempfile
                import zipfile
                import os
                
                manifest_json = """
                {
                    "version": "1.0.0",
                    "manifest_version": 2,
                    "name": "Chrome Proxy",
                    "permissions": [
                        "proxy",
                        "tabs",
                        "unlimitedStorage",
                        "storage",
                        "<all_urls>",
                        "webRequest",
                        "webRequestBlocking"
                    ],
                    "background": {
                        "scripts": ["background.js"]
                    },
                    "minimum_chrome_version":"22.0.0"
                }
                """

                background_js = """
                var config = {
                        mode: "fixed_servers",
                        rules: {
                          singleProxy: {
                            scheme: "%s",
                            host: "%s",
                            port: parseInt(%s)
                          },
                          bypassList: ["localhost"]
                        }
                      };

                chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});

                function callbackFn(details) {
                    return {
                        authCredentials: {
                            username: "%s",
                            password: "%s"
                        }
                    };
                }

                chrome.webRequest.onAuthRequired.addListener(
                            callbackFn,
                            {urls: ["<all_urls>"]},
                            ['blocking']
                );
                """
                
                proxy_url_parsed = urlparse(conf.proxy_url)
                scheme = proxy_url_parsed.scheme or "http"
                host = proxy_url_parsed.hostname
                port = proxy_url_parsed.port or 80

                formatted_bg_js = background_js % (scheme, host, port, conf.proxy_username, conf.proxy_password)

                plugin_dir = os.path.join(tempfile.gettempdir(), f"proxy_auth_plugin_{WORKER_ID}")
                os.makedirs(plugin_dir, exist_ok=True)
                
                with open(os.path.join(plugin_dir, "manifest.json"), "w") as f:
                    f.write(manifest_json)
                with open(os.path.join(plugin_dir, "background.js"), "w") as f:
                    f.write(formatted_bg_js)

                options.add_argument(f"--load-extension={plugin_dir}")
                
            self.driver = webdriver.Chrome(options=options)
            self.driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
            logger.info("Chrome browser started successfully")
        except Exception as e:
            logger.error(f"Failed to start Chrome browser: {e}")
            self.driver = None
            raise

    def is_alive(self) -> bool:
        """
        Verify that the Chrome browser process and WebDriver session are responsive.
        """
        if self.driver is None:
            return False
        try:
            # Check window handles or title
            _ = self.driver.current_window_handle
            return True
        except (WebDriverException, InvalidSessionIdException, NoSuchWindowException):
            return False
        except Exception:
            return False

    def navigate(self, url: str) -> None:
        """
        Navigate to a specific URL after validating domain authorization.
        """
        self._ensure_alive()

        # Domain restriction validation
        parsed = urlparse(url)
        if parsed.netloc:
            netloc = parsed.netloc.lower()
            if self._target_netloc and netloc != self._target_netloc and not netloc.endswith(f".{self._target_netloc}"):
                # Disallow navigation outside target domain
                raise ValueError(
                    f"Navigation to '{url}' rejected: outside authorized target domain '{TARGET_DOMAIN}'"
                )

        logger.info(f"Navigating to {url}")
        self.driver.get(url)

    def get_page_source(self) -> str:
        """
        Get the current page HTML source as a raw Python Unicode string.
        Does NOT unnecessarily encode/decode.
        """
        self._ensure_alive()
        return self.driver.page_source

    def get_current_url(self) -> str:
        """Get the current browser URL."""
        if not self.is_alive():
            return ""
        try:
            return self.driver.current_url
        except Exception:
            return ""

    def get_title(self) -> str:
        """Get the current page title."""
        if not self.is_alive():
            return ""
        try:
            return self.driver.title
        except Exception:
            return ""

    def take_screenshot_base64(self) -> str:
        """Take a screenshot of the current page and return as base64 string."""
        self._ensure_alive()
        return self.driver.get_screenshot_as_base64()

    def execute_script(self, script: str, *args: Any) -> Any:
        """Execute a JavaScript snippet within the browser context."""
        self._ensure_alive()
        return self.driver.execute_script(script, *args)

    def restart_with_config(self, config: BrowserConfig) -> None:
        """
        Update the current configuration and cleanly restart the Chrome session.
        """
        logger.warning(f"Applying new BrowserConfig: {config}")
        self._current_config = config
        self.restart()

    def restart(self) -> None:
        """
        Cleanly quit existing session if any and launch a fresh Chrome browser.
        """
        logger.warning("Restarting Chrome browser session...")
        self.quit()
        self.start()

    def quit(self) -> None:
        """
        Gracefully terminate the Chrome WebDriver session.
        """
        if self.driver is not None:
            try:
                self.driver.quit()
                logger.info("Chrome session terminated cleanly")
            except Exception as e:
                logger.debug(f"Error while quitting Chrome: {e}")
            finally:
                self.driver = None

    def _ensure_alive(self) -> None:
        """Check driver health and raise WebDriverException if session is dead."""
        if not self.is_alive():
            raise WebDriverException("Chrome WebDriver session is not active or has crashed")
