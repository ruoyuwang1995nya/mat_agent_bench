/* ───────────────────────────────
   questions.js — Question bank UI
─────────────────────────────── */

let allQuestions = [];

function toast(msg, type = 'info') {
  const c = document.getElementById('toasts');
  const t = document.createElement('div');
  t.className = `toast toast-${type}`;
  t.textContent = msg;
  c.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}

async function loadQuestions() {
  const cap = document.getElementById('filter-cap').value;
  const dom = document.getElementById('filter-dom').value;
  const params = new URLSearchParams();
  if (cap) params.set('capability', cap);
  if (dom) params.set('domain', dom);
  try {
    const res = await fetch(`/api/questions?${params}`);
    if (!res.ok) throw new Error(await res.text());
    allQuestions = await res.json();
    renderTable();
  } catch (err) {
    document.getElementById('q-body').innerHTML =
      `<tr><td colspan="6" style="text-align:center;color:var(--red);padding:2rem">${err.message}</td></tr>`;
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
      `<tr><td colspan="5" style="text-align:center;color:var(--text-dim);padding:3rem">No questions found.</td></tr>`;
    return;
  }

  document.getElementById('q-body').innerHTML = rows.map(q => `
    <tr data-id="${esc(q.id)}">
      <td><code style="font-size:.8rem;color:var(--cyan)">${esc(q.id)}</code></td>
      <td><span class="badge badge-cap">${esc(q.capability)}</span></td>
      <td><span class="badge badge-dom">${esc(q.domain)}</span></td>
      <td style="font-size:.78rem;color:var(--text-dim);max-width:240px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
        ${q.tags.slice(0, 5).map(t => esc(t)).join(', ')}${q.tag_count > 5 ? ' …' : ''}
      </td>
      <td style="text-align:center;font-family:var(--font-data);font-size:.8rem">${q.checklist_count}</td>
    </tr>
  `).join('');
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Upload
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

// Filters & search
document.getElementById('search').addEventListener('input', renderTable);
document.getElementById('filter-cap').addEventListener('change', loadQuestions);
document.getElementById('filter-dom').addEventListener('change', loadQuestions);

loadQuestions();
