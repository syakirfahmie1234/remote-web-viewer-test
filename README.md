# Remote Website Real-Time Automation & DOM Synchronization Relay

A high-performance, real-time website automation and DOM synchronization system built with Python, FastAPI, WebSockets, Selenium, and PySide6.

---

## 1. What This Project Is and Is Not

### What This Project IS:
- **Target-Specific Selenium Automation Relay**: Gated, robust remote control of website interactions via authenticated WebSockets.
- **Incremental DOM Synchronization System**: Real-time DOM diffing, stream normalization, and Zstandard-compressed snapshots and patch streaming (MSG_DOM_UPDATE / MSG_FULL_SNAPSHOT).
- **Multi-Worker Orchestration Layer**: Fully isolated worker processes where each Worker maintains its own persistent Chrome instance and independent monotonic DOM version clock.
- **Real-Time PySide6 Desktop Controller**: Multi-worker monitoring GUI with visual element picking, command queue dispatching, live DOM inspector, and bandwidth tracking.

### What This Project IS NOT:
- **NOT a generic remote browser, VNC, or AnyDesk**: Does not stream raw video frames or desktop captures.
- **NOT an arbitrary code execution platform**: Strictly executes allowlisted, structured JSON automation commands.
- **NOT a polling-based architecture**: Zero HTTP polling for real-time state; WebSocket (WSS) is the sole real-time transport.
- **NOT a shared-memory multi-threaded browser driver**: Workers never share Selenium/Chrome state.

---

## 2. Setup & Installation

### Prerequisites
- **Python**: 3.10, 3.11, 3.12, 3.13, or 3.14
- **Google Chrome**: Installed on the system path
- **ChromeDriver**: Compatible with installed Chrome version (automatically managed by Selenium 4.x)

### Installation
Clone the repository and install dependencies in editable mode:

`ash
# Clone repository
git clone <repo-url>
cd remote_website

# Install in editable mode
pip install -e .

# Or install from requirements.txt
pip install -r requirements.txt
`

### Environment Configuration
Copy .env.example to .env and configure your authentication secrets and endpoints:

`ash
cp .env.example .env
`

Key environment variables:
| Variable | Description | Default |
|---|---|---|
| PORT | Server listen port | 8000 |
| WORKER_TOKEN | Secret token for Worker authentication | change-me |
| CONTROLLER_TOKEN | Secret token for Controller authentication | change-me |
| TARGET_DOMAIN | Target website domain boundary | https://example.com |
| SERVER_WS_URL | WebSocket relay URL for workers/controllers | ws://127.0.0.1:8000/ws/worker |
| HEADLESS | Run Chrome in headless mode (	rue/alse) | 	rue |
| ZSTD_LEVEL | Zstandard compression level (1-9) | 3 |
| SESSION_TIMEOUT_SECONDS | Inactive controller session reap timeout | 3600 |

---

## 3. Running the System

You can run each component using the unified 
emote-website CLI or directly via python:

### A. Start the Relay Server
`ash
# Using CLI
remote-website server --port 8000

# Or via Python module
python -m server.main
`

### B. Start One or More Workers
Each worker runs in an independent process with an isolated Chrome session.

`ash
# Start Worker 1
remote-website worker --id worker-1 --target-domain https://example.com

# Start Worker 2 (in a separate terminal)
remote-website worker --id worker-2 --target-domain https://example.com

# Or via Python module
python -m worker.worker
`

### C. Launch the Desktop Controller GUI
`ash
# Using CLI
remote-website controller

# Or via Python module
python -m controller.controller
`

---

## 4. Running Tests & Benchmarks

The test suite validates protocol integrity, compression efficiency, DOM diff algorithms, crash recovery, multi-worker isolation, and performance benchmarks.

### Unit Tests
`ash
pytest tests/ --ignore=tests/test_e2e_integration.py -v
`

### End-to-End (E2E) Integration Tests
`ash
pytest tests/test_e2e_integration.py -v
`

### Performance & Throughput Benchmark Suite
`ash
python benchmarks/run_benchmarks.py
`

Benchmark Baseline Thresholds:
- **RTT Latency**: < 500ms
- **DOM Update Latency**: < 1000ms
- **Snapshot Delivery**: < 2000ms
- **Compression Ratio**: > 4.0x (typically ~55x via Zstandard)
- **Throughput**: > 3 cmds/sec per worker
- **Memory Usage**: < 1200MB per worker process

---

## 5. Architectural & Concurrency Invariants

### 1. Serialized Worker Execution (_selenium_lock)
Selenium's underlying ChromeDriver transport uses a single connection pool (urllib3 pool_maxsize=1). Attempting concurrent WebDriver operations against a single Chrome instance causes socket pool exhaustion and stalls the event loop.

To ensure stability:
- All Selenium interactions (commands, DOM extractions, and mutation polling) are gated behind an asynchronous _selenium_lock (syncio.Semaphore(1)).
- Incoming commands are queued and executed sequentially against the browser.
- **Horizontal Scaling Rule**: Throughput scales horizontally by adding more Worker processes, NOT by increasing per-worker thread concurrency.

### 2. Disjoint Lock Ordering (ABBA Prevention)
The Worker architecture enforces disjoint lock ownership:
- _selenium_lock: Acquired for WebDriver I/O, then **released**.
- _state_lock: Acquired separately to update internal Python state variables (dom_version, _last_snapshot_html).
- The two locks are never nested, guaranteeing zero deadlocks under heavy load.

---

## 6. Troubleshooting

### 1. Port Conflicts ([Errno 10048] / Address already in use)
- **Cause**: A previous Uvicorn server instance is still running on port 8000.
- **Fix**:
  `ash
  # Windows
  netstat -ano | findstr :8000
  taskkill /PID <PID> /F

  # Linux/macOS
  lsof -ti:8000 | xargs kill -9
  `

### 2. Zombie Chrome or ChromeDriver Processes
- **Cause**: Unclean termination during testing or debugger breakpoints.
- **Fix**:
  `ash
  # Windows
  taskkill /F /IM chrome.exe /IM chromedriver.exe

  # Linux/macOS
  killall -9 chrome chromedriver
  `

### 3. Missing or Mismatched Authentication Tokens
- **Symptom**: WebSocket connection rejected with 4003 AUTH_FAILED or 4001 UNAUTHORIZED.
- **Fix**: Verify that WORKER_TOKEN and CONTROLLER_TOKEN in your .env file match across all running processes.

### 4. Controller Cannot Connect to Remote Server
- **Symptom**: Controller fails to connect or logs ConnectionRefusedError.
- **Fix**: Ensure the SERVER_WS_URL parameter in the Controller points to ws://<server-host>:<port>/ws/controller (or wss://... for TLS-enabled deployments).

---

## License
MIT License.

## 7. Render Deployment

The relay server (FastAPI) is the only component deployed to Render.
Workers and the Controller always run locally — they connect outbound to the deployed server over WSS.

### Deploy Steps

1. Push this repository to GitHub.
2. Go to https://render.com → New → Blueprint → connect your repo.
   Render will detect `render.yaml` automatically.
3. Set the following environment variables in the Render dashboard (marked `sync: false` in render.yaml — these are secrets):
   - `WORKER_TOKEN` — any strong random string
   - `CONTROLLER_TOKEN` — any strong random string  
   - `TARGET_DOMAIN` — e.g. `https://yourtargetsite.com`
4. Deploy. Render will run:
   `pip install -r requirements-server.txt`
   `uvicorn server.main:app --host 0.0.0.0 --port $PORT`
5. Once deployed, copy your Render service URL (e.g. `https://remote-website-relay.onrender.com`).
6. Update your local `.env`:
   ```
   SERVER_WS_URL=wss://remote-website-relay.onrender.com/ws/worker
   CONTROLLER_WS_URL=wss://remote-website-relay.onrender.com/ws/controller
   ```

### Health Check
Render uses `GET /health` to verify the server is running.
This endpoint returns `{"status": "ok"}` and does NOT expose real-time state.

### What runs where
| Component | Runs on |
|---|---|
| FastAPI relay server | Render (cloud) |
| Worker (Selenium + Chrome) | Your local machine or private server |
| Controller (PySide6 GUI) | Your local machine |
