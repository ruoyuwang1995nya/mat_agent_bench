/* ───────────────────────────────
   questions.js — Question bank UI
─────────────────────────────── */

let allQuestions = [];
let benchmarkTemplate = '';
let questionTemplate = '';
let defaultServerUrl = 'http://127.0.0.1:8765';

fetch('/api/config')
  .then(r => r.json())
  .then(cfg => { if (cfg.server_url) defaultServerUrl = cfg.server_url; })
  .catch(() => {});

function toast(msg, type = 'info') {
  const c = document.getElementById('toasts');
  const t = document.createElement('div');
  t.className = `toast toast-${type}`;
  t.textContent = msg;
  c.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ─── Template loading ───────────────────────────────────────────────────────

async function loadTemplates() {
  try {
    const [bt, qt] = await Promise.all([
      fetch('/api/templates/benchmark').then(r => r.json()),
      fetch('/api/templates/question').then(r => r.json()),
    ]);
    benchmarkTemplate = bt.content;
    questionTemplate = qt.content;
  } catch (_) {
    // templates are optional; modal will show an error if unavailable
  }
}

// ─── Modal ──────────────────────────────────────────────────────────────────

function handleModalClick(e) {
  if (e.target === document.getElementById('prompt-modal')) closeModal();
}

function closeModal() {
  document.getElementById('prompt-modal').style.display = 'none';
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeModal();
});

function _renderModal(title, fields, templateFn) {
  document.getElementById('modal-title').textContent = title;

  const fieldsEl = document.getElementById('modal-fields');
  fieldsEl.innerHTML = fields.map(f => `
    <div class="modal-field-row">
      <label>${f.label}</label>
      <input id="modal-field-${f.key}" type="text" value="${esc(f.value)}"
             ${f.readonly ? 'readonly' : ''}
             placeholder="${esc(f.placeholder || '')}"
             oninput="_updateModalPrompt()">
    </div>
  `).join('');

  // store template fn so live-update can call it
  fieldsEl._templateFn = templateFn;
  _updateModalPrompt();

  document.getElementById('prompt-modal').style.display = 'flex';
}

function _getFieldValues() {
  const values = {};
  document.querySelectorAll('#modal-fields input').forEach(el => {
    const key = el.id.replace('modal-field-', '');
    values[key] = el.value;
  });
  return values;
}

function _updateModalPrompt() {
  const fn = document.getElementById('modal-fields')._templateFn;
  if (fn) {
    document.getElementById('modal-prompt').value = fn(_getFieldValues());
  }
}

function openRawPromptModal(questionId) {
  fetch(`/api/questions/${encodeURIComponent(questionId)}`)
    .then(r => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    })
    .then(data => {
      document.getElementById('modal-title').textContent = `PROMPT — ${questionId}`;
      document.getElementById('modal-fields').innerHTML = '';
      document.getElementById('modal-fields')._templateFn = null;
      document.getElementById('modal-prompt').value = data.prompt || '(no prompt)';
      document.getElementById('prompt-modal').style.display = 'flex';
    })
    .catch(err => toast(`Failed to load prompt: ${err.message}`, 'error'));
}

function openBenchmarkModal() {
  if (!benchmarkTemplate) {
    toast('Template not loaded yet', 'error');
    return;
  }
  _renderModal(
    'BENCHMARK PROMPT',
    [
      { key: 'server_url', label: 'Server URL', value: defaultServerUrl, placeholder: 'http://127.0.0.1:8765' },
    ],
    ({ server_url }) => {
      const url = server_url || defaultServerUrl;
      return benchmarkTemplate
        .replace(/\$SERVER_URL/g, url)
        .replace(/\{SERVER_URL\}/g, url);
    }
  );
}

function openQuestionModal(questionId) {
  if (!questionTemplate) {
    toast('Template not loaded yet', 'error');
    return;
  }
  _renderModal(
    `QUESTION PROMPT — ${questionId}`,
    [
      { key: 'question_id', label: 'Question ID', value: questionId, readonly: true },
      { key: 'server_url', label: 'Server URL', value: defaultServerUrl, placeholder: 'http://127.0.0.1:8765' },
      { key: 'token',      label: 'Token',      value: '', placeholder: 'your API token' },
      { key: 'session',    label: 'Session ID', value: '', placeholder: 'auto (leave blank for new session)' },
    ],
    ({ question_id, server_url, token, session }) => {
      const url = server_url || defaultServerUrl;
      return questionTemplate
        .replace(/\{QUESTION_ID\}/g, question_id || questionId)
        .replace(/\{SERVER_URL\}/g, url)
        .replace(/\{TOKEN\}/g, token || '<TOKEN>')
        .replace(/\{SESSION\}/g, session || '<SESSION>');
    }
  );
}

function copyPrompt() {
  const text = document.getElementById('modal-prompt').value;
  navigator.clipboard.writeText(text).then(
    () => toast('Copied to clipboard', 'success'),
    () => toast('Copy failed — select text manually', 'error')
  );
}

// ─── Questions table ─────────────────────────────────────────────────────────

async function loadQuestions() {
  const cap = document.getElementById('filter-cap').value;
  const type = document.getElementById('filter-type').value;
  const dom = document.getElementById('filter-dom').value;
  const params = new URLSearchParams();
  if (cap) params.set('capability', cap);
  if (type) params.set('task_type', type);
  if (dom) params.set('domain', dom);
  try {
    const res = await fetch(`/api/questions?${params}`);
    if (!res.ok) throw new Error(await res.text());
    allQuestions = await res.json();
    renderTable();
  } catch (err) {
    document.getElementById('q-body').innerHTML =
      `<tr><td colspan="7" style="text-align:center;color:var(--red);padding:2rem">${err.message}</td></tr>`;
  }
}

function renderTable() {
  const query = document.getElementById('search').value.toLowerCase();
  const rows = allQuestions.filter(q => {
    if (!query) return true;
    return q.id.toLowerCase().includes(query) ||
           q.intent.toLowerCase().includes(query) ||
           q.tags.some(t => t.toLowerCase().includes(query));
  });

  document.getElementById('count-label').textContent = `${rows.length} / ${allQuestions.length} questions`;

  if (!rows.length) {
    document.getElementById('q-body').innerHTML =
      `<tr><td colspan="7" style="text-align:center;color:var(--text-dim);padding:3rem">No questions found.</td></tr>`;
    return;
  }

  document.getElementById('q-body').innerHTML = rows.map(q => `
    <tr data-id="${esc(q.id)}">
      <td><code style="font-size:.8rem;color:var(--cyan)">${esc(q.id)}</code></td>
      <td><span class="badge badge-dom">${esc(q.domain)}</span></td>
      <td style="max-width:160px;white-space:normal"><span class="badge" style="background:var(--bg3);color:var(--text-dim);font-size:.72rem;white-space:normal">${esc(q.task_type.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase()))}</span></td>
      <td style="max-width:180px;white-space:normal">${(Array.isArray(q.capability) ? q.capability : [q.capability]).map(c => `<span class="badge badge-cap">${esc(c)}</span>`).join(' ')}</td>
      <td style="font-size:.78rem;color:var(--text-dim);max-width:220px;white-space:normal">
        ${q.tags.slice(0, 5).map(t => esc(t)).join(', ')}${q.tag_count > 5 ? ' …' : ''}
      </td>
      <td style="text-align:center;font-family:var(--font-data);font-size:.8rem">${q.checklist_count}</td>
      <td style="text-align:center">
        <button class="btn btn-sm" onclick="openRawPromptModal('${esc(q.id)}')" title="View question prompt">👁</button>
        <button class="btn btn-sm" onclick="openQuestionModal('${esc(q.id)}')" title="Show run prompt for this question">📋</button>
      </td>
    </tr>
  `).join('');
}

// ─── Upload ──────────────────────────────────────────────────────────────────

const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');

dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file) handleUpload(file);
});
fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) handleUpload(fileInput.files[0]);
});

async function handleUpload(file) {
  const resultEl = document.getElementById('upload-result');
  resultEl.style.display = 'block';
  resultEl.style.background = 'var(--bg2)';
  resultEl.style.border = '1px solid var(--border)';
  resultEl.style.color = 'var(--text-dim)';
  resultEl.textContent = `Uploading ${file.name}…`;

  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch('/api/questions/upload', { method: 'POST', body: form });
    const data = await res.json();
    if (res.ok) {
      resultEl.style.border = '1px solid var(--green)';
      resultEl.style.color = 'var(--green)';
      resultEl.textContent = `✓ Uploaded: ${data.id} (${data.capability} / ${data.domain})`;
      toast(`Added question ${data.id}`, 'success');
      loadQuestions();
    } else {
      const msg = typeof data === 'string' ? data : (data.detail || JSON.stringify(data));
      resultEl.style.border = '1px solid var(--red)';
      resultEl.style.color = 'var(--red)';
      resultEl.textContent = `✗ ${msg}`;
      toast('Upload failed', 'error');
    }
  } catch (err) {
    resultEl.style.border = '1px solid var(--red)';
    resultEl.style.color = 'var(--red)';
    resultEl.textContent = `✗ Network error: ${err.message}`;
    toast('Upload failed', 'error');
  }
  fileInput.value = '';
}

// ─── Filters & search ────────────────────────────────────────────────────────

document.getElementById('search').addEventListener('input', renderTable);
document.getElementById('filter-cap').addEventListener('change', loadQuestions);
document.getElementById('filter-type').addEventListener('change', loadQuestions);
document.getElementById('filter-dom').addEventListener('change', loadQuestions);

loadTemplates();
loadQuestions();
