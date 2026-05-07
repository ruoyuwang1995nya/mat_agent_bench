/* ─────────────────────────
   tokens.js — Tokens page
────────────────────────── */

let currentUser = null;

function toast(msg, type = 'info') {
  const c = document.getElementById('toasts');
  const t = document.createElement('div');
  t.className = `toast toast-${type}`;
  t.textContent = msg;
  c.appendChild(t);
  setTimeout(() => t.remove(), 5000);
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function init() {
  currentUser = await requireAuth();
  if (!currentUser) return;
  document.getElementById('nav-user').textContent = currentUser;
  loadTokens();
}

async function loadTokens() {
  try {
    const res = await fetch('/api/tokens');
    if (res.status === 401) { window.location.replace('/static/login.html'); return; }
    if (!res.ok) throw new Error(await res.text());
    renderTokens(await res.json());
  } catch (err) {
    document.getElementById('token-body').innerHTML =
      `<tr><td colspan="5" style="text-align:center;color:var(--red);padding:2rem">${esc(err.message)}</td></tr>`;
  }
}

function renderTokens(tokens) {
  if (!tokens.length) {
    document.getElementById('token-body').innerHTML =
      `<tr><td colspan="5" style="text-align:center;color:var(--text-dim);padding:3rem">
        No tokens yet. Enter an agent name above and click <strong>Generate</strong>.
      </td></tr>`;
    return;
  }

  document.getElementById('token-body').innerHTML = tokens.map(t => {
    const date = t.created_at ? new Date(t.created_at).toLocaleString() : '—';
    const models = t.models.length
      ? t.models.map(m => `<span class="badge badge-cap" style="font-size:.7rem">${esc(m)}</span>`).join(' ')
      : '<span style="color:var(--text-dim)">—</span>';
    return `
      <tr>
        <td style="font-weight:600;color:var(--cyan)">${esc(t.agent_name)}</td>
        <td>
          <div style="display:flex;align-items:center;gap:.5rem">
            <code id="tok-${esc(t.token.slice(0,8))}"
              style="font-family:var(--font-data);font-size:.78rem;color:var(--text-dim);word-break:break-all">
              ${esc(t.token)}
            </code>
            <button class="btn btn-sm" style="flex-shrink:0"
              onclick="copyTok('${esc(t.token)}', this)">Copy</button>
          </div>
        </td>
        <td style="font-size:.82rem;color:var(--text-dim)">${date}</td>
        <td style="text-align:center;font-family:var(--font-data);font-size:.85rem">${t.evaluation_count}</td>
        <td style="font-size:.8rem">${models}</td>
      </tr>
    `;
  }).join('');
}

async function requestToken() {
  const agentName = document.getElementById('agent-name').value.trim();
  const errEl = document.getElementById('req-error');
  errEl.style.display = 'none';

  if (!agentName) {
    errEl.textContent = 'Please enter an agent name.';
    errEl.style.display = 'block';
    return;
  }

  const btn = document.getElementById('req-btn');
  btn.disabled = true;
  btn.textContent = 'Generating…';

  try {
    const res = await fetch('/api/tokens', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_name: agentName }),
    });
    const data = await res.json();
    if (!res.ok) {
      errEl.textContent = data.detail || JSON.stringify(data);
      errEl.style.display = 'block';
      return;
    }
    document.getElementById('agent-name').value = '';
    toast(`Token created for "${agentName}"`, 'success');
    loadTokens();
  } catch (err) {
    errEl.textContent = `Error: ${err.message}`;
    errEl.style.display = 'block';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Generate';
  }
}

function copyTok(token, btn) {
  navigator.clipboard.writeText(token).then(() => {
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = orig; }, 2000);
  });
}

init();
