import os
import sys
import subprocess
import time
import asyncio
import json
import urllib.request
import urllib.error
import websockets
from shared.messages import parse_message, FullSnapshotMessage, WorkerStatusMessage
from shared.protocol import STATUS_CRASHED, STATUS_CONNECTED

def wait_for_server(port=8000, timeout=10):
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/")
            return True
        except urllib.error.URLError:
            time.sleep(0.5)
    return False

async def run_e2e_test():
    worker_id = "test-config-stress"
    controller_token = "test-controller-456"
    
    print("[Test] Starting standalone server on port 8003 for stress test...")
    server_proc = subprocess.Popen([sys.executable, "-m", "server.main"],
                                   env=dict(os.environ, PORT="8003", CONTROLLER_TOKEN=controller_token, WORKER_TOKEN="test-worker-123"))
    if not wait_for_server(port=8003):
        print("Server failed to start")
        server_proc.kill()
        return

    worker_proc = subprocess.Popen([sys.executable, "-m", "worker.worker"],
                                   env=dict(os.environ, WORKER_ID=worker_id, WORKER_TOKEN="test-worker-123", SERVER_WS_URL="ws://127.0.0.1:8003/ws/worker"))
    
    try:
        uri = f"ws://127.0.0.1:8003/ws/controller?token={controller_token}&client_id=test-controller-stress"
        async with websockets.connect(uri) as ws:
            print("[Test] Controller connected.")
            # Wait for worker to connect to the server first
            print("[Test] Waiting 5 seconds for worker to boot and connect...")
            await asyncio.sleep(5)
            
            # Subscribe to worker
            await ws.send(json.dumps({
                "type": "controller_register",
                "message_id": "req-1",
                "timestamp": "2026-09-12T12:00:00Z",
                "protocol_version": 1,
                "subscribed_worker_ids": [worker_id]
            }))
            
            # Request initial sync
            await ws.send(json.dumps({
                "type": "resync_request",
                "worker_id": worker_id,
                "reason": "initial",
                "message_id": "req-sync-1",
                "timestamp": "2026-09-12T12:00:00Z",
                "protocol_version": 1,
            }))
            
            # Wait for initial connection
            connected = False
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=20)
                parsed = parse_message(json.loads(msg))
                if isinstance(parsed, FullSnapshotMessage):
                    print("[Test] Initial FULL_SNAPSHOT received. Worker is ready.")
                    connected = True
                    break
            
            assert connected, "Worker did not send full snapshot"
            
            print("[Test] Beginning rapid configuration stress test (5 consecutive flips)...")
            headless_state = True
            
            for i in range(5):
                headless_state = not headless_state
                print(f"[Test] Iteration {i+1}: Sending BrowserConfigMessage (headless={headless_state})")
                
                await ws.send(json.dumps({
                    "type": "browser_config",
                    "worker_id": worker_id,
                    "headless": headless_state,
                    "proxy_url": None,
                    "message_id": f"req-config-{i}",
                    "timestamp": "2026-09-12T12:00:00Z",
                    "protocol_version": 1,
                }))
                
                # Now we must observe exactly STATUS_CRASHED followed by STATUS_CONNECTED
                got_crashed = False
                got_connected = False
                got_snapshot = False
                
                start_wait = time.time()
                while time.time() - start_wait < 15:
                    msg = await asyncio.wait_for(ws.recv(), timeout=15)
                    parsed = parse_message(json.loads(msg))
                    if isinstance(parsed, WorkerStatusMessage):
                        if parsed.status == STATUS_CRASHED:
                            print(f"  -> Worker is restarting Chrome (STATUS_CRASHED)")
                            got_crashed = True
                        elif parsed.status == STATUS_CONNECTED:
                            print(f"  -> Worker recovered successfully (STATUS_CONNECTED)")
                            got_connected = True
                    elif isinstance(parsed, FullSnapshotMessage):
                        print(f"  -> Received new FULL_SNAPSHOT for iteration {i+1}")
                        got_snapshot = True
                    
                    if got_crashed and got_connected and got_snapshot:
                        break
                
                assert got_crashed, "Worker did not send STATUS_CRASHED"
                assert got_connected, "Worker did not send STATUS_CONNECTED (the bug wasn't fixed!)"
                assert got_snapshot, "Worker did not send FULL_SNAPSHOT after restart"
                print(f"[Test] Iteration {i+1} PASSED.")
                
            print("[Test] SUCCESS! Stress test completed. All configurations applied smoothly.")
            
    finally:
        print("[Test] Tearing down processes...")
        worker_proc.terminate()
        server_proc.terminate()
        worker_proc.wait()
        server_proc.wait()

def main():
    asyncio.run(run_e2e_test())

if __name__ == "__main__":
    main()
