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
  loadSessions();
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

async function loadSessions() {
  try {
    const res = await fetch('/api/sessions');
    if (res.status === 401) { window.location.replace('/static/login.html'); return; }
    if (!res.ok) throw new Error(await res.text());
    renderSessions(await res.json());
  } catch (err) {
    document.getElementById('session-body').innerHTML =
      `<tr><td colspan="7" style="text-align:center;color:var(--red);padding:2rem">${esc(err.message)}</td></tr>`;
  }
}

function renderSessions(sessions) {
  if (!sessions.length) {
    document.getElementById('session-body').innerHTML =
      `<tr><td colspan="8" style="text-align:center;color:var(--text-dim);padding:3rem">
        No sessions yet. Run an evaluation to see results here.
      </td></tr>`;
    return;
  }

  document.getElementById('session-body').innerHTML = sessions.map(s => {
    const date = s.created_at ? new Date(s.created_at).toLocaleString() : '—';
    const score = s.weighted_score != null ? (s.weighted_score * 100).toFixed(1) + '%' : '—';
    const models = s.models.length
      ? s.models.map(m => `<span class="badge badge-cap" style="font-size:.7rem">${esc(m)}</span>`).join(' ')
      : '<span style="color:var(--text-dim)">—</span>';
    const shortId = s.session_id.length > 16 ? s.session_id.slice(0, 14) + '…' : s.session_id;
    const sidEsc = esc(s.session_id);
    const agentEsc = esc(s.agent_name);
    return `
      <tr>
        <td>
          <div style="display:flex;align-items:center;gap:.4rem">
            <code style="font-family:var(--font-data);font-size:.78rem;color:var(--text-dim)" title="${sidEsc}">${esc(shortId)}</code>
            <button class="btn btn-sm" style="flex-shrink:0;padding:.15rem .45rem;font-size:.7rem"
              onclick="copySid('${sidEsc}', this)">Copy</button>
          </div>
        </td>
        <td style="font-weight:600;color:var(--cyan)">${agentEsc}</td>
        <td style="text-align:center;font-family:var(--font-data);font-size:.85rem">${s.question_count}</td>
        <td style="text-align:center;font-family:var(--font-data);font-size:.85rem">${s.eval_count}</td>
        <td style="font-family:var(--font-data);font-size:.85rem;color:var(--cyan)">${score}</td>
        <td style="font-size:.8rem">${models}</td>
        <td style="font-size:.82rem;color:var(--text-dim)">${date}</td>
        <td style="text-align:center">
          <button class="btn btn-sm" style="padding:.15rem .45rem;font-size:.7rem"
            onclick="downloadSessionCSV('${sidEsc}', '${agentEsc}')">&#8659; CSV</button>
        </td>
      </tr>
    `;
  }).join('');
}

function copySid(sid, btn) {
  navigator.clipboard.writeText(sid).then(() => {
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = orig; }, 2000);
  });
}

async function downloadSessionCSV(sessionId, agentName) {
  try {
    const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/questions`);
    if (res.status === 401) { window.location.replace('/static/login.html'); return; }
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    const questions = data.questions || [];
    if (!questions.length) { toast('No results for this session.', 'info'); return; }
    const headers = ['question_id','capability','domain','mode','runs','passed','total','pass_rate','overall','safety_vetoed','criteria_detail'];
    const lines = [headers.join(',')];
    for (const q of questions) {
      const detail = (q.criteria_detail || []).map(c => {
        const verdict = c.passed === c.total ? 'PASS' : (c.passed === 0 ? 'FAIL' : `${c.passed}/${c.total}`);
        const reason = (c.reasons || []).join('; ');
        return reason ? `${c.criterion_id}(${c.capability || ''}):${verdict}:${reason}` : `${c.criterion_id}(${c.capability || ''}):${verdict}`;
      }).join(' | ');
      const row = { ...q, criteria_detail: detail };
      lines.push(headers.map(h => {
        const v = String(row[h] ?? '');
        return v.includes(',') || v.includes('"') ? `"${v.replace(/"/g,'""')}"` : v;
      }).join(','));
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${agentName}_${sessionId}_questions.csv`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    toast(`CSV download failed: ${err.message}`, 'error');
  }
}
