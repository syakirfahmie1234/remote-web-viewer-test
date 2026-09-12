
function generateMessageId() {
    return Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15);
}

let ws = null;
let activeWorkerId = null;
let liveMode = false;
let currentUrl = "";
let currentHtml = "";

const loginScreen = document.getElementById('login-screen');
const mainUi = document.getElementById('main-ui');
const tokenInput = document.getElementById('token-input');
const btnLogin = document.getElementById('btn-login');
const loginError = document.getElementById('login-error');

const workerSelect = document.getElementById('worker-select');
const statusBadge = document.getElementById('status-badge');
const urlInput = document.getElementById('url-input');
const viewport = document.getElementById('viewport');
const chkLive = document.getElementById('chk-live');

// Load interceptor.js as text so we can inject it
let interceptorScript = "";
fetch('/static/interceptor.js').then(r => r.text()).then(t => interceptorScript = t);

btnLogin.addEventListener('click', () => {
    const token = tokenInput.value.trim();
    if (!token) return;
    
    loginError.classList.add('hidden');
    btnLogin.disabled = true;
    btnLogin.textContent = "Connecting...";

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/controller`;
    
    ws = new WebSocket(wsUrl);
    
    ws.onopen = () => {
        // Send HELLO
        ws.send(JSON.stringify({type: 'auth', token: token, protocol_version: 1, message_id: generateMessageId(), timestamp: new Date().toISOString()}));
        // Send HELLO
        ws.send(JSON.stringify({type: 'hello', role: 'controller', protocol_version: 1, message_id: generateMessageId(), timestamp: new Date().toISOString()}));
        // Send CONTROLLER_REGISTER
        ws.send(JSON.stringify({type: 'controller_register', protocol_version: 1, message_id: generateMessageId(), timestamp: new Date().toISOString()}));

    };
    
    ws.onmessage = async (evt) => {
        let msgStr;
        if (evt.data instanceof Blob) {
            // Unlikely for JSON messages, but just in case
            msgStr = await evt.data.text();
        } else {
            msgStr = evt.data;
        }
        
        let msg;
        try { msg = JSON.parse(msgStr); } catch(e) { return; }
        
        // Any valid message from server means authentication succeeded!
        if (!loginScreen.classList.contains('hidden')) {
            loginScreen.classList.add('hidden');
            mainUi.classList.remove('hidden');
        }
        handleMessage(msg);
    };
    
    ws.onerror = (e) => {
        console.error("WS Error:", e);
    };
    
    ws.onclose = (e) => {
        console.log("WS Closed:", e.code);
        btnLogin.disabled = false;
        btnLogin.textContent = "Login";
        mainUi.classList.add('hidden');
        loginScreen.classList.remove('hidden');
        if (e.code === 1008) {
            loginError.textContent = "Invalid token. Policy violation.";
        } else {
            loginError.textContent = "Connection closed.";
        }
        loginError.classList.remove('hidden');
        ws = null;
    };
});

function handleMessage(msg) {
    if (msg.type === 'worker_status') {
        let opt = workerSelect.querySelector(`option[value="${msg.worker_id}"]`);
        if (!opt) {
            opt = document.createElement('option');
            opt.value = msg.worker_id;
            workerSelect.appendChild(opt);
        }
        opt.textContent = `${msg.worker_id} (${msg.status})`;
        
        // Auto-select first worker if none selected
        if (!activeWorkerId || activeWorkerId === msg.worker_id) {
            workerSelect.value = msg.worker_id;
            activeWorkerId = msg.worker_id;
            updateStatusBadge(msg.status);
        }
    } else if (msg.type === 'full_snapshot') {
        if (msg.worker_id !== activeWorkerId) return;
        
        let htmlStr = msg.html;
        if (msg.encoding === 'zstd' && window.fzstd) {
            try {
                const compressedBytes = Uint8Array.from(atob(msg.html), c => c.charCodeAt(0));
                const decompressedBytes = window.fzstd.decompress(compressedBytes);
                htmlStr = new TextDecoder().decode(decompressedBytes);
            } catch (e) {
                console.error("Failed to decompress zstd payload:", e);
                return;
            }
        } else if (msg.encoding === 'base64') {
            htmlStr = decodeURIComponent(escape(window.atob(msg.html)));
        }
        
        currentHtml = htmlStr;
        currentUrl = msg.url || "";
        urlInput.value = currentUrl;
        updateStatusBadge('sync');
        renderViewport();
    } else if (msg.type === 'dom_update') {
        if (msg.worker_id !== activeWorkerId) return;
        // The Python GUI does VDOM patching. In simple JS, we can just request a full resync for now, 
        // OR rely on the fact that if a dom_update arrives, the backend sends a full_snapshot if we ask.
        // Actually, let's just ask for a resync on dom_update to keep JS simple.
        sendCommand("resync_request");
    }
}

function updateStatusBadge(status) {
    statusBadge.className = 'badge';
    if (status === 'sync' || status === 'idle' || status === 'connected') {
        statusBadge.textContent = 'Synchronized';
        statusBadge.classList.add('sync');
    } else if (status === 'busy') {
        statusBadge.textContent = 'Busy';
        statusBadge.classList.add('stale');
    } else {
        statusBadge.textContent = status.toUpperCase();
    }
}

function renderViewport() {
    if (liveMode) {
        if (viewport.src !== currentUrl) {
            viewport.src = currentUrl;
        }
    } else {
        // Inject interceptor
        let finalHtml = currentHtml;
        const scriptTag = `<script>${interceptorScript}<\/script>`;
        if (finalHtml.includes('</body>')) {
            finalHtml = finalHtml.replace('</body>', scriptTag + '</body>');
        } else {
            finalHtml += scriptTag;
        }
        viewport.srcdoc = finalHtml;
    }
}

function sendCommand(cmd, payload = {}) {
    if (!ws || !activeWorkerId) return;
    
    if (cmd === 'resync_request') {
        ws.send(JSON.stringify({
            message_id: generateMessageId(),
            timestamp: new Date().toISOString(),
            protocol_version: 1,
            type: "resync_request",
            worker_id: activeWorkerId
        }));
        return;
    }
    
    ws.send(JSON.stringify({
        message_id: generateMessageId(),
        timestamp: new Date().toISOString(),
        protocol_version: 1,
        type: "command",
        worker_id: activeWorkerId,
        command: cmd,
        payload: payload
    }));
}

// UI EVENT LISTENERS
workerSelect.addEventListener('change', (e) => {
    activeWorkerId = e.target.value;
    sendCommand("resync_request");
});

document.getElementById('btn-back').addEventListener('click', () => sendCommand('back'));
document.getElementById('btn-forward').addEventListener('click', () => sendCommand('forward'));
document.getElementById('btn-refresh').addEventListener('click', () => sendCommand('refresh'));
document.getElementById('btn-resync').addEventListener('click', () => sendCommand('resync_request'));

document.getElementById('btn-go').addEventListener('click', () => {
    sendCommand('navigate', { url: urlInput.value.trim() });
});
urlInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        sendCommand('navigate', { url: urlInput.value.trim() });
    }
});

chkLive.addEventListener('change', (e) => {
    liveMode = e.target.checked;
    renderViewport();
});

// INTERCEPTOR MESSAGES FROM IFRAME
window.addEventListener('message', (e) => {
    if (e.data && e.data.type === 'ANTIGRAVITY_CMD') {
        sendCommand(e.data.cmd, e.data);
    }
});
