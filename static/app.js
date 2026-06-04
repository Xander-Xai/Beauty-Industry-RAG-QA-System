const chatMessages = document.getElementById('chat-messages');
const queryInput = document.getElementById('query-input');
const sendBtn = document.getElementById('send-btn');
const clearBtn = document.getElementById('clear-btn');
const userSelect = document.getElementById('user-select');
const healthStatus = document.getElementById('health-status');
const latencyDisplay = document.getElementById('latency-display');

let sessionId = 'session_' + Date.now();
let isLoading = false;

// --- Health Check ---
async function checkHealth() {
    try {
        const res = await fetch('/api/health');
        if (res.ok) {
            healthStatus.textContent = '正常';
            healthStatus.className = 'status ok';
        } else {
            healthStatus.textContent = '异常';
            healthStatus.className = 'status error';
        }
    } catch {
        healthStatus.textContent = '离线';
        healthStatus.className = 'status error';
    }
}
checkHealth();
setInterval(checkHealth, 30000);

// --- Get User Identity ---
function getIdentity() {
    const opt = userSelect.selectedOptions[0];
    return {
        role: parseInt(opt.dataset.role),
        dept: parseInt(opt.dataset.dept),
    };
}

// --- Send Message ---
async function sendMessage(text) {
    if (isLoading || !text.trim()) return;
    isLoading = true;
    sendBtn.disabled = true;

    // Add user message
    addMessage('user', text);
    queryInput.value = '';
    queryInput.style.height = 'auto';

    // Show thinking
    const thinkingEl = addThinking();

    const identity = getIdentity();
    const startTime = Date.now();

    try {
        const res = await fetch('/api/query', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-User-ID': 'web_user',
                'X-Role-Mask': String(identity.role),
                'X-Dept-Mask': String(identity.dept),
            },
            body: JSON.stringify({ query: text, session_id: sessionId }),
        });

        const latency = Date.now() - startTime;
        latencyDisplay.textContent = `${latency}ms`;

        thinkingEl.remove();

        if (res.ok) {
            const data = await res.json();
            const meta = `耗时 ${latency}ms${data.business_type ? ' · ' + data.business_type : ''}`;
            addMessage('assistant', data.answer || data.response || '（无回答内容）', meta);
        } else if (res.status === 429) {
            addMessage('error', '请求过于频繁，请稍后再试。');
        } else {
            const err = await res.json().catch(() => ({}));
            addMessage('error', `请求失败 (${res.status}): ${err.detail || '未知错误'}`);
        }
    } catch (e) {
        thinkingEl.remove();
        addMessage('error', `网络错误: ${e.message}`);
    } finally {
        isLoading = false;
        sendBtn.disabled = false;
        queryInput.focus();
    }
}

// --- UI Helpers ---
function addMessage(role, text, meta = '') {
    // Remove welcome message on first real message
    const welcome = chatMessages.querySelector('.welcome-message');
    if (welcome) welcome.remove();

    const div = document.createElement('div');
    div.className = `message ${role}`;
    div.innerHTML = text.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    if (meta) {
        div.innerHTML += `<div class="meta">${meta}</div>`;
    }
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return div;
}

function addThinking() {
    const div = document.createElement('div');
    div.className = 'thinking';
    div.innerHTML = '<span></span><span></span><span></span>';
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return div;
}

// --- Event Listeners ---
sendBtn.addEventListener('click', () => sendMessage(queryInput.value));

queryInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage(queryInput.value);
    }
});

// Auto-resize textarea
queryInput.addEventListener('input', () => {
    queryInput.style.height = 'auto';
    queryInput.style.height = Math.min(queryInput.scrollHeight, 120) + 'px';
});

clearBtn.addEventListener('click', () => {
    chatMessages.innerHTML = '';
    sessionId = 'session_' + Date.now();
    latencyDisplay.textContent = '-';
});

// Quick question buttons
document.querySelectorAll('.quick-q').forEach(btn => {
    btn.addEventListener('click', () => sendMessage(btn.dataset.q));
});
