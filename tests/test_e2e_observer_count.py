import os
import sys
import subprocess
import time
import asyncio
import json
import urllib.request
import urllib.error
import websockets
from shared.messages import parse_message, FullSnapshotMessage, ObserverCountMessage, CommandMessage
from shared.protocol import MSG_FULL_SNAPSHOT, MSG_WORKER_STATUS, STATUS_CONNECTED

def wait_for_server(port=8002, timeout=10):
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/")
            return True
        except urllib.error.URLError:
            time.sleep(0.5)
    return False

async def run_e2e_test():
    worker_id = "test-worker-alpha"
    controller_token = "test-controller-456"
    
    print("[Test] Connecting to existing server on port 8000...")

    worker_proc = subprocess.Popen([sys.executable, "-m", "worker.worker"],
                                   env=dict(os.environ, WORKER_ID=worker_id, WORKER_TOKEN="test-worker-123", SERVER_WS_URL="ws://127.0.0.1:8000/ws/worker"))
    
    try:
        # Step 1: Connect controller
        uri = f"ws://127.0.0.1:8000/ws/controller?token={controller_token}&client_id=test-controller-1"
        async with websockets.connect(uri) as ws:
            print("[Test] Controller connected.")
            # Subscribe to worker
            await ws.send(json.dumps({
                "type": "controller_register",
                "message_id": "req-1",
                "timestamp": "2026-09-12T12:00:00Z",
                "protocol_version": 1,
                "subscribed_workers": [worker_id]
            }))
            
            # Wait for FULL_SNAPSHOT
            connected = False
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=20)
                parsed = parse_message(json.loads(msg))
                if isinstance(parsed, FullSnapshotMessage):
                    print(f"[Test] Got full snapshot: {parsed.url}")
                    connected = True
                    break
            
            assert connected, "Worker did not send full snapshot"
            
            # Navigate to example.com
            await ws.send(json.dumps({
                "type": "command",
                "worker_id": worker_id,
                "command": "navigate",
                "payload": {"url": "https://example.com"},
                "message_id": "req-nav",
                "timestamp": "2026-09-12T12:00:00Z",
                "protocol_version": 1,
            }))
            
            print("[Test] Sent navigate to example.com. Waiting 4 seconds...")
            await asyncio.sleep(4)
            
        # Step 2: Controller disconnected. Worker should get observer_count=0
        print("[Test] Controller disconnected. Waiting 7 seconds for auto-cleanup...")
        await asyncio.sleep(7.0)
        
        # Step 3: Reconnect and verify
        async with websockets.connect(uri) as ws2:
            print("[Test] Controller reconnected.")
            await ws2.send(json.dumps({
                "type": "controller_register",
                "message_id": "req-2",
                "timestamp": "2026-09-12T12:00:00Z",
                "protocol_version": 1,
                "subscribed_workers": [worker_id]
            }))
            
            # Request resync to get latest state
            await ws2.send(json.dumps({
                "type": "resync_request",
                "worker_id": worker_id,
                "reason": "test",
                "message_id": "req-3",
                "timestamp": "2026-09-12T12:00:00Z",
                "protocol_version": 1,
            }))
            
            target_url = None
            while True:
                msg = await asyncio.wait_for(ws2.recv(), timeout=15)
                parsed = parse_message(json.loads(msg))
                if isinstance(parsed, FullSnapshotMessage):
                    target_url = parsed.url
                    print(f"[Test] Got snapshot after reconnect: {target_url}")
                    break
            
            assert "new-tab-page" in target_url or "newtab" in target_url or "chrome://" in target_url, f"Worker did not auto-navigate to new tab page! Still at: {target_url}"
            print("[Test] SUCCESS! Worker auto-navigated to new tab page after 5 seconds of idle.")
            
    finally:
        print("[Test] Tearing down processes...")
        worker_proc.terminate()
        worker_proc.wait()

def main():
    asyncio.run(run_e2e_test())

if __name__ == "__main__":
    main()
