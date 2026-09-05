import asyncio
from worker.browser import BrowserManager, BrowserConfig
from worker.command_handler import CommandHandler
from shared.messages import CommandMessage
from worker.worker import Worker

async def main():
    class FakeWS:
        async def send_message(self, msg):
            print(f"WS SENT: {type(msg).__name__} -> {msg}")
            
    worker = Worker("w1")
    worker.ws_client = FakeWS()
    
    config = BrowserConfig(headless=True)
    worker.browser._current_config = config
    worker.browser.start()
    worker.browser.navigate("https://the-internet.herokuapp.com/javascript_alerts")
    await asyncio.sleep(2)
    
    print("Page loaded successfully.")
    
    # We simulate a click to trigger an alert
    handler = CommandHandler(worker.browser)
    cmd = CommandMessage("w1", "cmd1", "click", {"selector": "//button[text()='Click for JS Alert']", "is_xpath": True})
    print("Executing command to click alert button...")
    res = await asyncio.to_thread(handler.execute, cmd)
    print("Command result:", res.success)
    
    # Now trigger mutation polling!
    # Because an alert is open, get_page_source should throw!
    print("Polling mutations...")
    worker.dom_tracker.base_html = "dummy"
    worker.browser._navigation_in_progress = False
    
    # Run one tick of poll_mutations manually
    try:
        page_source = worker.browser.get_page_source()
        print("Page source grabbed:", len(page_source))
    except Exception as e:
        print(f"Exception while grabbing page source: {type(e).__name__} - {e}")
        from selenium.common.exceptions import UnexpectedAlertPresentException
        if isinstance(e, UnexpectedAlertPresentException):
            alert_text = getattr(e, 'alert_text', 'Unknown alert text')
            if not alert_text or alert_text == 'Unknown alert text':
                try:
                    alert_text = worker.browser.driver.switch_to.alert.text
                except Exception:
                    pass
            print(f"Alert text: {alert_text}")
            from shared.messages import create_alert_opened, create_worker_status
            from shared.protocol import STATUS_ALERT_BLOCKING
            msg1 = create_alert_opened(worker.worker_id, alert_text)
            msg2 = create_worker_status(worker.worker_id, STATUS_ALERT_BLOCKING)
            print(f"Created messages: {msg1}, {msg2}")
    
    worker.browser.stop()

asyncio.run(main())
