const state = {
  folders: [],
  selectedFolder: null,
  tiers: [],
  defaultTier: null,
  jobs: {},
  eventSources: {},
  openLogs: new Set(),
};

function el(id) { return document.getElementById(id); }
function esc(s) {
  return String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

async function api(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = j.detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  return res.json();
}

function fmtSize(n) {
  if (n == null) return '';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(1) + u[i];
}

function fmtDuration(s) {
  s = Math.round(s);
  if (s < 60) return s + ' 秒';
  if (s < 3600) return Math.floor(s / 60) + ' 分 ' + (s % 60) + ' 秒';
  return Math.floor(s / 3600) + ' 小时 ' + Math.floor((s % 3600) / 60) + ' 分';
}

function statusLabel(s) {
  return ({
    queued: '排队中', running: '解析中', done: '完成',
    failed: '失败', cancelled: '已取消', interrupted: '已中断', cleaned: '已清理',
  })[s] || s;
}

// ---- 配置 ----
async function loadConfig() {
  const cfg = await api('/api/config');
  state.tiers = cfg.tiers;
  state.defaultTier = cfg.default_tier;
  const sel = el('tier');
  sel.innerHTML = '';
  for (const b of cfg.tiers) {
    const o = document.createElement('option');
    o.value = b; o.textContent = b;
    if (b === cfg.default_tier) o.selected = true;
    sel.appendChild(o);
  }
}

// ---- 文件夹 ----
async function loadFolders() {
  const data = await api('/api/folders');
  state.folders = data.folders;
  renderFolders();
}

function renderFolders() {
  const list = el('folderList');
  if (!state.folders.length) {
    list.innerHTML = '<p class="muted">还没有文件夹，先上传一个 ZIP</p>';
    updateParseBtn();
    return;
  }
  list.innerHTML = '';
  for (const f of state.folders) {
    const div = document.createElement('div');
    div.className = 'folder' + (state.selectedFolder === f.name ? ' selected' : '');
    div.innerHTML = `
      <div class="folder-main">
        <span class="folder-name">📁 ${esc(f.name)}</span>
        <span class="folder-files">${f.files} 个文件</span>
      </div>
      <div class="folder-actions">
        <button class="btn-ghost delete-btn" data-name="${esc(f.name)}">删除</button>
      </div>`;
    div.addEventListener('click', (e) => {
      if (e.target.closest('.delete-btn')) return;
      state.selectedFolder = f.name;
      renderFolders();
    });
    div.querySelector('.delete-btn').addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm(`删除文件夹「${f.name}」？`)) return;
      await api('/api/folders/delete', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: f.name }),
      });
      if (state.selectedFolder === f.name) state.selectedFolder = null;
      loadFolders();
    });
    list.appendChild(div);
  }
  updateParseBtn();
}

function updateParseBtn() {
  el('parseBtn').disabled = !state.selectedFolder;
}

// ---- 上传 ----
function uploadZip(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.zip')) { alert('只支持 .zip 文件'); return; }
  const wrap = el('uploadProgressWrap');
  const bar = el('uploadBar');
  const txt = el('uploadText');
  wrap.classList.remove('hidden');
  bar.style.width = '0%';

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/upload');
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      const pct = Math.round(e.loaded / e.total * 100);
      bar.style.width = pct + '%';
      txt.textContent = `上传中 ${pct}% (${fmtSize(e.loaded)} / ${fmtSize(e.total)})`;
    }
  };
  xhr.onload = () => {
    if (xhr.status >= 200 && xhr.status < 300) {
      const j = JSON.parse(xhr.responseText);
      bar.style.width = '100%';
      txt.textContent = `✅ 已解压为「${j.name}」（${j.files} 个文件）`;
      setTimeout(() => { wrap.classList.add('hidden'); bar.style.width = '0%'; }, 2000);
      loadFolders();
    } else {
      let msg = '上传失败';
      try { msg = JSON.parse(xhr.responseText).detail || msg; } catch (e) {}
      txt.textContent = '❌ ' + msg;
    }
  };
  xhr.onerror = () => { txt.textContent = '❌ 网络错误'; };
  const fd = new FormData();
  fd.append('file', file);
  xhr.send(fd);
}

function setupUpload() {
  const dz = el('dropzone');
  const input = el('fileInput');
  dz.addEventListener('click', () => input.click());
  input.addEventListener('change', () => { if (input.files[0]) uploadZip(input.files[0]); input.value = ''; });
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('drag'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
  dz.addEventListener('drop', (e) => {
    e.preventDefault();
    dz.classList.remove('drag');
    if (e.dataTransfer.files[0]) uploadZip(e.dataTransfer.files[0]);
  });
}

// ---- 任务 ----
async function startParse() {
  if (!state.selectedFolder) return;
  const tier = el('tier').value;
  try {
    const res = await api('/api/parse', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder: state.selectedFolder, tier }),
    });
    connectJob(res.job_id);
  } catch (e) { alert(e.message); }
}

function connectJob(jobId) {
  if (state.eventSources[jobId]) return;
  const es = new EventSource(`/api/jobs/${jobId}/events`);
  state.eventSources[jobId] = es;
  es.onmessage = (e) => {
    const job = JSON.parse(e.data);
    state.jobs[job.id] = job;
    renderJobs();
    if (['cleaned', 'failed', 'cancelled', 'interrupted'].includes(job.status)) {
      es.close();
      delete state.eventSources[jobId];
    }
  };
}

async function loadJobs() {
  const data = await api('/api/jobs');
  for (const job of data.jobs) {
    state.jobs[job.id] = job;
    if (['running', 'queued', 'done'].includes(job.status)) connectJob(job.id);
  }
  renderJobs();
}

function renderJobs() {
  const list = el('jobList');
  const jobs = Object.values(state.jobs).sort((a, b) => b.created_at - a.created_at);
  if (!jobs.length) { list.innerHTML = '<p class="muted">暂无任务</p>'; return; }
  list.innerHTML = '';
  for (const job of jobs) list.appendChild(renderJob(job));
}

function renderJob(job) {
  const card = document.createElement('div');
  card.className = 'job ' + job.status;
  const pct = job.progress_percent ?? 0;
  const running = job.status === 'running' || job.status === 'queued';
  const logHtml = (job.log || []).slice(-40).map(l => `<div class="log-line">${esc(l)}</div>`).join('');
  const logOpen = state.openLogs.has(job.id);
  const perFile = job.per_file_sec ? fmtDuration(job.per_file_sec) : null;
  const eta = job.eta_sec ? fmtDuration(job.eta_sec) : null;

  card.innerHTML = `
    <div class="job-head">
      <span class="job-name">📄 ${esc(job.name)}</span>
      <span class="badge badge-${job.status}">${statusLabel(job.status)}</span>
    </div>
    <div class="bar"><div class="bar-fill" style="width:${pct}%"></div></div>
    <div class="job-stats">
      <span class="stat-big">${pct}%</span>
      <span class="stat-line">已处理 <b>${job.done_files}</b> / ${job.total_files} 个文件</span>
      ${running && !perFile ? '<span class="stat-line">⏱ 正在初始化模型（约 3 分钟）…</span>' : ''}
      ${perFile ? `<span class="stat-line">⏱ 平均每文件 ~${perFile}</span>` : ''}
      ${running && eta ? `<span class="stat-line">🕐 预计还需 ~${eta}</span>` : ''}
    </div>
    ${job.inputs_cleaned ? `<div class="hint-clean">🧹 原始 PDF 已自动清理，仅保留解析结果</div>` : ''}
    ${job.error ? `<div class="error">❌ ${esc(job.error)}</div>` : ''}
    <div class="job-actions">
      ${running ? `<button class="btn cancel" data-id="${job.id}">取消</button>` : ''}
      <button class="btn-ghost log-toggle" data-id="${job.id}">${logOpen ? '📋 收起日志' : '📋 查看日志'}</button>
      ${job.status === 'done' ? `
        <a class="btn download" href="/api/jobs/${job.id}/download">⬇ 下载结果 ZIP</a>
        <button class="btn ghost-delete" data-id="${job.id}">🗑 删除结果</button>` : ''}
    </div>
    <div class="log-wrap ${logOpen ? '' : 'hidden'}"><pre class="log">${logHtml}</pre></div>
  `;
  const cancelBtn = card.querySelector('.cancel');
  if (cancelBtn) cancelBtn.addEventListener('click', () => api(`/api/jobs/${job.id}/cancel`, { method: 'POST' }));
  const delBtn = card.querySelector('.ghost-delete');
  if (delBtn) delBtn.addEventListener('click', async () => {
    if (!confirm('删除该任务的解析结果？删除后需重新解析才能再次下载。')) return;
    await api(`/api/jobs/${job.id}/delete`, { method: 'POST' });
  });
  const logToggle = card.querySelector('.log-toggle');
  if (logToggle) logToggle.addEventListener('click', () => {
    if (state.openLogs.has(job.id)) state.openLogs.delete(job.id);
    else state.openLogs.add(job.id);
    renderJobs();
  });
  if (logOpen) {
    const pre = card.querySelector('.log');
    if (pre) pre.scrollTop = pre.scrollHeight;
  }
  return card;
}

// ---- 初始化 ----
async function init() {
  setupUpload();
  el('parseBtn').addEventListener('click', startParse);
  await loadConfig();
  await loadFolders();
  await loadJobs();
}

document.addEventListener('DOMContentLoaded', init);
