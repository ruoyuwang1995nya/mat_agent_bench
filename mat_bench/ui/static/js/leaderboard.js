/* ─────────────────────────────────
   leaderboard.js — Leaderboard UI
────────────────────────────────── */

let allEntries = [];
let chart = null;
let _detailData = [];  // cached for CSV export

function toast(msg, type = 'info') {
  const c = document.getElementById('toasts');
  const t = document.createElement('div');
  t.className = `toast toast-${type}`;
  t.textContent = msg;
  c.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function loadLeaderboard() {
  try {
    const res = await fetch('/api/leaderboard');
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();

    document.getElementById('stat-total-eval').textContent = data.total_evaluations ?? 0;
    document.getElementById('stat-run-count').textContent = data.run_count ?? 0;
    document.getElementById('stat-model-count').textContent = data.leaderboard?.length ?? 0;

    allEntries = data.leaderboard ?? [];
    renderLeaderboard();
  } catch (err) {
    document.getElementById('lb-body').innerHTML =
      `<tr><td colspan="6" style="text-align:center;color:var(--red);padding:2rem">${err.message}</td></tr>`;
    toast('Failed to load leaderboard', 'error');
  }
}

function renderLeaderboard() {
  const capFilter = document.getElementById('filter-cap').value;

  let entries = allEntries;

  // When a capability filter is active, sort by that capability's score instead
  if (capFilter) {
    entries = entries
      .filter(e => e.by_capability && capFilter in e.by_capability)
      .map(e => ({ ...e, _sort_score: e.by_capability[capFilter] }))
      .sort((a, b) => b._sort_score - a._sort_score);
  }

  // Update chart title
  const titleEl = document.getElementById('chart-title');
  if (titleEl) titleEl.textContent = capFilter ? `Score (${capFilter}) by Agent` : 'Questions Passed by Agent';

  // Build chart
  buildChart(entries, capFilter);

  if (!entries.length) {
    document.getElementById('lb-body').innerHTML =
      `<tr><td colspan="6" style="text-align:center;color:var(--text-dim);padding:4rem">No evaluation data found.<br>
      <small>Run <code style="color:var(--cyan)">mat-bench serve</code> or <code style="color:var(--cyan)">mat-bench run</code> to generate results.</small>
      </td></tr>`;
    return;
  }

  const medals = ['🥇', '🥈', '🥉'];
  const maxPassed = Math.max(1, ...entries.map(e => e.questions_passed || 0));

  document.getElementById('lb-body').innerHTML = entries.map((e, i) => {
    const score = capFilter ? (e.by_capability[capFilter] ?? 0) : e.questions_passed;
    const displayScore = capFilter ? (score * 100).toFixed(1) + '%' : String(score);
    const barWidth = capFilter ? score * 100 : (score / maxPassed) * 100;
    const rank = i + 1;
    const rankDisplay = rank <= 3
      ? `<span title="Rank ${rank}">${medals[rank-1]}</span>`
      : `<span class="badge badge-rank">${rank}</span>`;

    // Top 3 capabilities by score
    const topCaps = Object.entries(e.by_capability || {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([cap, s]) => `<span class="badge badge-cap" title="${cap}: ${(s*100).toFixed(1)}%">${cap.replace('_',' ')}</span>`)
      .join(' ');

    return `
      <tr>
        <td style="text-align:center">${rankDisplay}</td>
        <td>
          <div style="font-family:var(--font-data);font-size:.9rem;color:var(--text)">${esc(e.agent)}</div>
          <div style="margin-top:.25rem">${(e.models || []).map(m => `<span class="badge" style="font-size:.7rem;opacity:.7;font-family:var(--font-data)">${esc(m)}</span>`).join(' ')}</div>
        </td>
        <td>
          <div class="score-bar-wrap">
            <div class="score-bar"><div class="score-bar-fill" style="width:${barWidth}%"></div></div>
            <div class="score-val">${displayScore}</div>
          </div>
        </td>
        <td style="text-align:center;font-family:var(--font-data);font-size:.85rem">${e.total_evaluations}</td>
        <td style="font-size:.78rem">${topCaps || '<span style="color:var(--text-dim)">—</span>'}</td>
        <td style="text-align:center">
          <button class="btn" style="font-size:.75rem;padding:.25rem .6rem"
            data-agent="${e.agent.replace(/&/g,'&amp;').replace(/"/g,'&quot;')}"
            onclick="showDetails(this.dataset.agent)">Details</button>
        </td>
      </tr>
    `;
  }).join('');
}

async function showDetails(agentName) {
  document.getElementById('modal-title').textContent = `${agentName} — per-question results`;
  document.getElementById('modal-body').innerHTML = '<div style="text-align:center;padding:2rem"><div class="spinner" style="margin:auto"></div></div>';
  document.getElementById('detail-modal').style.display = 'flex';

  try {
    const res = await fetch(`/api/leaderboard/${encodeURIComponent(agentName)}/questions`);
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    _detailData = data.questions || [];
    renderDetailTable(_detailData);
  } catch (err) {
    document.getElementById('modal-body').innerHTML =
      `<p style="color:var(--red);padding:1rem">${err.message}</p>`;
    toast('Failed to load details', 'error');
  }
}

function renderDetailTable(questions) {
  if (!questions.length) {
    document.getElementById('modal-body').innerHTML = '<p style="padding:1rem;color:var(--text-dim)">No question results found.</p>';
    return;
  }
  const rows = questions.map(q => {
    const isPass = q.passed === q.total && q.total > 0;
    const statusBadge = isPass
      ? `<span style="color:var(--green);font-weight:700">&#10003; PASS</span>`
      : `<span style="color:var(--red);font-weight:700">&#10007; FAIL</span>`;
    const pct = q.total > 0 ? (q.pass_rate * 100).toFixed(0) + '%' : '—';
    const safety = q.safety_vetoed ? '<span style="color:var(--yellow);font-size:.7rem">&#9888; vetoed</span>' : '';
    return `<tr>
      <td style="font-family:var(--font-data);font-size:.8rem">${esc(q.question_id)}</td>
      <td style="font-size:.78rem">${esc(q.capability.replace('_',' '))}</td>
      <td style="font-size:.78rem">${esc(q.domain)}</td>
      <td style="font-size:.78rem;text-align:center">${esc(q.mode)}</td>
      <td style="text-align:center;font-family:var(--font-data);font-size:.8rem">${q.runs}</td>
      <td style="text-align:center;font-family:var(--font-data);font-size:.8rem">${q.passed}/${q.total}</td>
      <td style="text-align:center;font-family:var(--font-data);font-size:.8rem">${pct}</td>
      <td style="font-size:.78rem;text-align:center">${esc(q.correctness)}</td>
      <td style="font-size:.78rem;text-align:center">${esc(q.grounding)}</td>
      <td style="font-size:.78rem;text-align:center">${esc(q.efficiency)}</td>
      <td style="text-align:center">${statusBadge} ${safety}</td>
    </tr>`;
  }).join('');

  document.getElementById('modal-body').innerHTML = `
    <table style="width:100%;border-collapse:collapse;font-size:.82rem">
      <thead>
        <tr style="border-bottom:1px solid rgba(0,240,255,.2)">
          <th style="text-align:left;padding:.4rem .5rem">Question</th>
          <th style="text-align:left;padding:.4rem .5rem">Capability</th>
          <th style="text-align:left;padding:.4rem .5rem">Domain</th>
          <th style="text-align:center;padding:.4rem .5rem">Mode</th>
          <th style="text-align:center;padding:.4rem .5rem">Runs</th>
          <th style="text-align:center;padding:.4rem .5rem">Passed</th>
          <th style="text-align:center;padding:.4rem .5rem">Rate</th>
          <th style="text-align:center;padding:.4rem .5rem">Correctness</th>
          <th style="text-align:center;padding:.4rem .5rem">Grounding</th>
          <th style="text-align:center;padding:.4rem .5rem">Efficiency</th>
          <th style="text-align:center;padding:.4rem .5rem">Status</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function closeModal() {
  document.getElementById('detail-modal').style.display = 'none';
  _detailData = [];
}

function closeModalOnBg(event) {
  if (event.target === document.getElementById('detail-modal')) closeModal();
}

function downloadCSV() {
  if (!_detailData.length) return;
  const headers = ['question_id','capability','domain','mode','runs','passed','total','pass_rate','correctness','grounding','efficiency','safety_vetoed'];
  const lines = [headers.join(',')];
  for (const q of _detailData) {
    lines.push(headers.map(h => {
      const v = String(q[h] ?? '');
      return v.includes(',') || v.includes('"') ? `"${v.replace(/"/g,'""')}"` : v;
    }).join(','));
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  const agentName = document.getElementById('modal-title').textContent.split(' —')[0].trim();
  a.download = `${agentName}_questions.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function buildChart(entries, capFilter) {
  const labels = entries.map(e => e.agent.length > 24 ? e.agent.slice(0, 22) + '…' : e.agent);
  const isPercent = !!capFilter;
  const rawScores = entries.map(e => capFilter ? (e.by_capability[capFilter] ?? 0) : (e.questions_passed ?? 0));
  const chartData = isPercent ? rawScores.map(s => +(s * 100).toFixed(2)) : rawScores;

  const ctx = document.getElementById('score-chart').getContext('2d');
  if (chart) chart.destroy();

  chart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: capFilter ? `Score (${capFilter})` : 'Questions Passed',
        data: chartData,
        backgroundColor: chartData.map((_, i) => {
          const hue = 180 + (i / Math.max(chartData.length - 1, 1)) * 80;
          return `hsla(${hue}, 100%, 60%, 0.45)`;
        }),
        borderColor: chartData.map((_, i) => {
          const hue = 180 + (i / Math.max(chartData.length - 1, 1)) * 80;
          return `hsla(${hue}, 100%, 70%, 0.9)`;
        }),
        borderWidth: 1,
        borderRadius: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      scales: {
        y: {
          beginAtZero: true,
          ...(isPercent ? { max: 100 } : {}),
          ticks: {
            color: 'rgba(255,255,255,0.45)',
            callback: v => isPercent ? v + '%' : v,
          },
          grid: { color: 'rgba(0,240,255,0.07)' },
          border: { color: 'rgba(0,240,255,0.15)' },
        },
        x: {
          ticks: { color: 'rgba(255,255,255,0.6)', maxRotation: 30 },
          grid: { display: false },
          border: { color: 'rgba(0,240,255,0.15)' },
        },
      },
      plugins: {
        legend: { labels: { color: 'rgba(255,255,255,0.6)', font: { size: 12 } } },
        tooltip: {
          callbacks: {
            label: ctx => isPercent ? ` ${ctx.parsed.y.toFixed(1)}%` : ` ${ctx.parsed.y}`,
          },
        },
      },
    },
  });
}

document.getElementById('filter-cap').addEventListener('change', renderLeaderboard);

loadLeaderboard();

// Poll for new results every 30 seconds
setInterval(loadLeaderboard, 30_000);

