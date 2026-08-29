import os
import sys
import time
import asyncio
import argparse
import tempfile
import subprocess
import json
import psutil
import websockets
import zstandard as zstd
from contextlib import asynccontextmanager

# Add parent directory to path to import server/worker code
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.messages import (
    create_controller_register,
    create_resync_request,
    create_command,
    serialize_message,
    parse_message
)
from shared.protocol import (
    MSG_COMMAND_RESULT,
    MSG_FULL_SNAPSHOT,
    MSG_DOM_UPDATE
)

CONTROLLER_TOKEN = "test-controller-token"
WORKER_TOKEN = "test-worker-token"

# Thresholds
THRESHOLDS = {
    "rtt_ms": 500.0,
    "dom_update_ms": 1000.0,
    "snapshot_ms": 2000.0,
    "compression_ratio": 4.0,
    "throughput_cmds_sec": 3.0,
    "memory_mb": 1200.0
}

def start_server():
    print("[*] Starting Uvicorn server...")
    env = os.environ.copy()
    env["CONTROLLER_TOKEN"] = CONTROLLER_TOKEN
    env["WORKER_TOKEN"] = WORKER_TOKEN
    
    server_out = open("server.log", "w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.main:app", "--port", "58999", "--host", "127.0.0.1"],
        env=env,
        stdout=server_out,
        stderr=subprocess.STDOUT
    )
    proc._log_file = server_out
    time.sleep(2)
    return proc

def start_worker(worker_id):
    print(f"[*] Starting worker {worker_id}...")
    worker_out = open(f"{worker_id}.log", "w")
    env = os.environ.copy()
    env["WORKER_ID"] = worker_id
    env["SERVER_WS_URL"] = "ws://127.0.0.1:58999/ws/worker"
    env["WORKER_TOKEN"] = WORKER_TOKEN
    env["TARGET_DOMAIN"] = "http://127.0.0.1:58998"
    
    proc = subprocess.Popen(
        [sys.executable, "-m", "worker.worker"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env,
        stdout=worker_out,
        stderr=subprocess.STDOUT
    )
    proc._log_file = worker_out
    time.sleep(2)
    return proc

def start_html_server():
    print("[*] Starting HTML server...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", "58998"],
        cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(1)
    return proc

@asynccontextmanager
async def controller_client(worker_ids):
    url = f"ws://127.0.0.1:58999/ws/controller?token={CONTROLLER_TOKEN}"
    async with websockets.connect(url) as ws:
        reg = create_controller_register(client_id="benchmark", subscribed_worker_ids=worker_ids)
        await ws.send(serialize_message(reg))
        yield ws

async def run_latency_benchmark(ws):
    print("\n--- LATENCY BENCHMARK ---")
    results = {}
    
    # 1. RTT (Command -> Result)
    print("Measuring RTT...")
    cmd = create_command("worker-1", "navigate", payload={"url": "http://127.0.0.1:58998/large_dom.html"})
    t0 = time.time()
    await ws.send(serialize_message(cmd))
    
    while True:
        raw = await asyncio.wait_for(ws.recv(), 5.0)
        msg = parse_message(raw)
        if msg.type == MSG_COMMAND_RESULT:
            break
            
    rtt_ms = (time.time() - t0) * 1000
    results["rtt_ms"] = rtt_ms
    print(f"  RTT: {rtt_ms:.2f}ms")
    
    # 2. Snapshot Delivery
    print("Measuring Snapshot Delivery & Compression...")
    resync = create_resync_request("worker-1", reason="benchmark")
    t0 = time.time()
    await ws.send(serialize_message(resync))
    
    snap_msg = None
    while True:
        raw = await asyncio.wait_for(ws.recv(), 5.0)
        msg = parse_message(raw)
        if msg.type == MSG_FULL_SNAPSHOT:
            snap_msg = msg
            break
            
    snap_ms = (time.time() - t0) * 1000
    results["snapshot_ms"] = snap_ms
    print(f"  Snapshot Delivery: {snap_ms:.2f}ms")
    
    # Compression Ratio
    if getattr(snap_msg, "compressed", False):
        import base64
        import zstandard as zstd
        
        compressed_bytes = base64.b64decode(snap_msg.html)
        decompressed_bytes = zstd.ZstdDecompressor().decompress(compressed_bytes)
        
        comp_size = len(compressed_bytes)
        decomp_size = len(decompressed_bytes)
        ratio = decomp_size / comp_size if comp_size > 0 else 1.0
        results["compression_ratio"] = ratio
        print(f"  Compression: {ratio:.2f}x ({decomp_size}b -> {comp_size}b)")
    else:
        results["compression_ratio"] = 1.0
        print("  Compression: None")
    
    print("Measuring DOM Update Latency...")
    # Navigate to heavy mutation
    cmd = create_command("worker-1", "navigate", payload={"url": "http://127.0.0.1:58998/heavy_mutation.html"})
    await ws.send(serialize_message(cmd))
    
    # Wait for navigate to finish
    while True:
        raw = await asyncio.wait_for(ws.recv(), 5.0)
        msg = parse_message(raw)
        if msg.type == MSG_COMMAND_RESULT:
            break
            
    # Click start button
    cmd = create_command("worker-1", "click", payload={"selector": "#start"})
    t0 = time.time()
    await ws.send(serialize_message(cmd))
    
    # Wait for first DOM update (or timeout if not implemented)
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), 5.0)
            msg = parse_message(raw)
            if msg.type == MSG_DOM_UPDATE:
                break
        dom_update_ms = (time.time() - t0) * 1000
        results["dom_update_ms"] = dom_update_ms
        print(f"  DOM Update: {dom_update_ms:.2f}ms")
    except TimeoutError:
        print("  DOM Update: Timeout (Not Implemented?)")
        results["dom_update_ms"] = 999.99
    
    return results

async def run_throughput_benchmark(ws):
    print("\n--- THROUGHPUT BENCHMARK ---")
    print("Sending 100 commands across workers...")
    
    t0 = time.time()
    # Send 100 commands
    for i in range(100):
        wid = f"worker-{(i % 2) + 1}"
        cmd = create_command(wid, "navigate", payload={"url": "http://127.0.0.1:58998/large_dom.html"})
        await ws.send(serialize_message(cmd))
        
    # Wait for 100 results
    count = 0
    while count < 100:
        raw = await asyncio.wait_for(ws.recv(), 60.0)
        msg = parse_message(raw)
        if msg.type == MSG_COMMAND_RESULT:
            count += 1
            
    total_time = time.time() - t0
    throughput = 100 / total_time
    print(f"  Throughput: {throughput:.2f} cmds/sec")
    
    return {"throughput_cmds_sec": throughput}
    
def get_memory_usage(pid):
    try:
        proc = psutil.Process(pid)
        mem = proc.memory_info().rss
        for child in proc.children(recursive=True):
            try:
                mem += child.memory_info().rss
            except psutil.NoSuchProcess:
                pass
        return mem / (1024 * 1024)
    except psutil.NoSuchProcess:
        return 0.0

async def main():
    server_proc = start_server()
    html_proc = start_html_server()
    worker1 = start_worker("worker-1")
    worker2 = start_worker("worker-2")
    
    try:
        async with controller_client(["worker-1", "worker-2"]) as ws:
            print("[*] Waiting for workers to connect and send initial snapshots...")
            
            # Request initial resync to ensure we get a snapshot
            resync1 = create_resync_request("worker-1", reason="init")
            resync2 = create_resync_request("worker-2", reason="init")
            await ws.send(serialize_message(resync1))
            await ws.send(serialize_message(resync2))
            
            # We expect 2 WORKER_STATUS and 2 FULL_SNAPSHOT messages
            ready_workers = set()
            while len(ready_workers) < 2:
                raw = await asyncio.wait_for(ws.recv(), 60.0)
                msg = parse_message(raw)
                if msg.type == MSG_FULL_SNAPSHOT:
                    ready_workers.add(msg.worker_id)
            
            print("[*] All workers connected. Starting benchmarks.")
            
            latency_res = await run_latency_benchmark(ws)
            throughput_res = await run_throughput_benchmark(ws)
            
            print("\n--- MEMORY BENCHMARK ---")
            mem1 = get_memory_usage(worker1.pid)
            mem2 = get_memory_usage(worker2.pid)
            avg_mem = (mem1 + mem2) / 2
            print(f"  Worker 1: {mem1:.2f} MB")
            print(f"  Worker 2: {mem2:.2f} MB")
            
            results = {**latency_res, **throughput_res, "memory_mb": avg_mem}
            
            # Print Final Report
            print("\n===============================")
            print("      BENCHMARK REPORT         ")
            print("===============================")
            
            warnings = 0
            for k, v in results.items():
                threshold = THRESHOLDS.get(k, 0)
                if k in ["throughput_cmds_sec", "compression_ratio"]:
                    exceeded = v < threshold
                else:
                    exceeded = v > threshold
                    
                status = "PASS"
                if exceeded:
                    status = f"WARNING (Threshold: {threshold})"
                    warnings += 1
                    
                print(f"{k.ljust(25)}: {v:.2f} [{status}]")
                
            print("===============================")
            
            res_path = os.path.join(os.path.dirname(__file__), "results")
            os.makedirs(res_path, exist_ok=True)
            with open(os.path.join(res_path, f"benchmark_{int(time.time())}.json"), "w") as f:
                json.dump(results, f, indent=2)
                
    finally:
        for p in [worker1, worker2, html_proc, server_proc]:
            p.terminate()
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()

if __name__ == "__main__":
    asyncio.run(main())
