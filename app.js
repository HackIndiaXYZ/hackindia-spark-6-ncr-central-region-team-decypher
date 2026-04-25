const API = 'http://127.0.0.1:8000';

/* ── STATE ─────────────────────────────────────────────────────────────── */
const state = {
  tasks: [],
  dashboard: null,
  gmailConnected: false,
  calOffset: 0,
  currentPage: 'home',
  filterTab: 'all',
  currentReminderTask: null,
};

const AUTH_USERS_KEY = 'taskmind_users';
const AUTH_SESSION_KEY = 'taskmind_session';
const TASKS_KEY = 'taskmind_tasks';

function getUsers() {
  try { return JSON.parse(localStorage.getItem(AUTH_USERS_KEY) || '[]'); }
  catch (_) { return []; }
}

function saveUsers(users) {
  localStorage.setItem(AUTH_USERS_KEY, JSON.stringify(users));
}

function currentUser() {
  try { return JSON.parse(localStorage.getItem(AUTH_SESSION_KEY) || 'null'); }
  catch (_) { return null; }
}

function saveSession(user) {
  localStorage.setItem(AUTH_SESSION_KEY, JSON.stringify(user));
}

function logout() {
  localStorage.removeItem(AUTH_SESSION_KEY);
  window.location.href = 'signup.html';
}

function requireAuth() {
  const page = location.pathname.split('/').pop() || 'index.html';
  if (page === 'index.html' && !currentUser()) window.location.replace('signup.html');
}

function taskStorageKey() {
  const user = currentUser();
  return user?.id ? `${TASKS_KEY}_${user.id}` : TASKS_KEY;
}

function updateUserUI() {
  const user = currentUser();
  const avatar = document.querySelector('.avatar');
  if (avatar && user) {
    const initials = `${user.fname?.[0] || ''}${user.lname?.[0] || ''}` || user.email?.[0] || 'AI';
    avatar.textContent = initials.toUpperCase();
    avatar.title = user.email;
  }
}

/* ── DOM REFS ───────────────────────────────────────────────────────────── */
const $ = id => document.getElementById(id);
const toastStack = $('toast-stack');

/* ── TOAST ──────────────────────────────────────────────────────────────── */
function toast(msg, type = 'info') {
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  toastStack.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

/* ── THEME ──────────────────────────────────────────────────────────────── */
function initTheme() {
  const saved = localStorage.getItem('theme') || 'light';
  document.documentElement.setAttribute('data-theme', saved);
  updateThemeIcon(saved);
}
function toggleTheme() {
  const cur = document.documentElement.getAttribute('data-theme');
  const next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
  updateThemeIcon(next);
}
function updateThemeIcon(theme) {
  const btn = $('theme-btn');
  if (btn) btn.textContent = theme === 'dark' ? '☀️' : '🌙';
}

/* ── NAVIGATION ─────────────────────────────────────────────────────────── */
function showPage(pageId) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  const page = $(`page-${pageId}`);
  if (page) { page.classList.add('active'); state.currentPage = pageId; }
  const btn = $(`nav-${pageId}`);
  if (btn) btn.classList.add('active');
  if (pageId === 'calendar') renderCalendar();
  if (pageId === 'analytics') renderAnalytics();
  if (pageId === 'tasks') renderTasksPage();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function saveTasks() {
  localStorage.setItem(taskStorageKey(), JSON.stringify(state.tasks));
}

function loadSavedTasks() {
  try {
    state.tasks = (JSON.parse(localStorage.getItem(taskStorageKey()) || '[]') || []).map(normalizeTask);
  } catch (_) {
    state.tasks = [];
  }
}

function taskDate(t) {
  if (t.deadline_iso) {
    const d = new Date(t.deadline_iso);
    if (!Number.isNaN(d.getTime())) return d;
  }
  return null;
}

function sameDay(a, b) {
  return a && b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function dateKey(d) {
  if (!d) return '';
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

function startOfDay(d) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

function buildTaskAnalytics() {
  const now = new Date();
  const total = state.tasks.length;
  const done = state.tasks.filter(t => t.done).length;
  const dated = state.tasks.map(t => ({ task: t, date: taskDate(t) })).filter(x => x.date);
  const today = dated.filter(x => sameDay(x.date, now)).length;
  const overdue = dated.filter(x => startOfDay(x.date) < startOfDay(now) && !x.task.done).length;
  const weekEnd = new Date(startOfDay(now)); weekEnd.setDate(weekEnd.getDate() + 7);
  const week = dated.filter(x => x.date >= startOfDay(now) && x.date <= weekEnd).length;
  const completion = total ? Math.round((done / total) * 100) : 0;
  const chart = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(startOfDay(now)); d.setDate(d.getDate() + i);
    return {
      label: d.toLocaleDateString('en-IN', { weekday: 'short' }),
      value: dated.filter(x => sameDay(x.date, d)).length,
      completed: dated.filter(x => sameDay(x.date, d) && x.task.done).length,
    };
  });
  return { total, today, overdue, week, done, completion, chart };
}

/* ── MODAL HELPERS ──────────────────────────────────────────────────────── */
function openModal(id) {
  const m = $(id);
  if (m) { m.classList.add('open'); document.body.style.overflow = 'hidden'; }
}
function closeModal(id) {
  const m = $(id);
  if (m) { m.classList.remove('open'); document.body.style.overflow = ''; }
}

/* ── ADD TASK MODAL TABS ────────────────────────────────────────────────── */
function switchModalTab(tab) {
  document.querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelector(`.modal-tab[data-tab="${tab}"]`)?.classList.add('active');
  $(`tab-${tab}`)?.classList.add('active');
}

/* ── ESCAPE HTML ────────────────────────────────────────────────────────── */
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

/* ── PRIORITY BADGE ─────────────────────────────────────────────────────── */
function priorityBadge(p) {
  const c = { HIGH: 'badge-high', MEDIUM: 'badge-medium', LOW: 'badge-low' };
  return `<span class="badge ${c[p] || 'badge-low'}">${esc(p || 'LOW')}</span>`;
}

function priorityFromTask(t) {
  const p = String(t.priority || 'LOW').toUpperCase();
  return ['HIGH', 'MEDIUM', 'LOW'].includes(p) ? p : 'LOW';
}

/* ── SOURCE BADGE ───────────────────────────────────────────────────────── */
function sourceBadge(s) {
  const map = { email: 'badge-gmail', gmail: 'badge-gmail', screenshot: 'badge-ocr', ocr: 'badge-ocr', whatsapp: 'badge-wa', slack: 'badge-slack' };
  const cls = map[(s || '').toLowerCase()] || 'badge-source';
  const icon = { email: '📧', gmail: '📧', screenshot: '📷', ocr: '📷', whatsapp: '💬', slack: '#' }[(s||'').toLowerCase()] || '📝';
  return `<span class="badge ${cls}">${icon} ${esc(s || 'text')}</span>`;
}

/* ── RENDER TASKS HOME ──────────────────────────────────────────────────── */
function renderHomeTaskPreview() {
  const el = $('home-task-list');
  if (!el) return;
  const tasks = state.tasks.slice().reverse();
  if (!tasks.length) {
    el.innerHTML = `<div class="empty-state"><div class="empty-icon">✅</div><p class="empty-title">No tasks yet</p><p class="empty-sub">Add your first task using the button above</p></div>`;
    return;
  }
  el.innerHTML = tasks.map(t => taskItemHTML(t, state.tasks.indexOf(t))).join('');
}

/* ── TASK ITEM HTML ─────────────────────────────────────────────────────── */
function taskItemHTML(t, i) {
  const done = t.done ? 'done' : '';
  return `<div class="task-item" id="task-item-${i}">
    <div class="task-check ${done}" onclick="toggleTask(${i})">${done ? '✓' : ''}</div>
    <div class="task-info">
      <div class="task-title" style="${done ? 'text-decoration:line-through;opacity:.5' : ''}">${esc(t.task || t.title || 'Untitled')}</div>
      <div class="task-meta">
        ${priorityBadge(priorityFromTask(t))}
        ${t.priority_reason ? `<span style="font-size:12px;color:var(--muted)">AI: ${esc(t.priority_reason)}</span>` : ''}
        ${sourceBadge(t.source)}
        ${t.deadline ? `<span style="font-size:12px;color:var(--muted)">📅 ${esc(t.deadline)}</span>` : ''}
      </div>
    </div>
    <div class="task-actions">
      <button class="task-remind-btn" onclick="openReminderFor(${i})">🔔 Remind</button>
    </div>
  </div>`;
}

/* ── TOGGLE TASK DONE ───────────────────────────────────────────────────── */
function toggleTask(i) {
  if (!state.tasks[i]) return;
  state.tasks[i].done = !state.tasks[i].done;
  saveTasks();
  renderHomeTaskPreview();
  renderTasksPage();
  updateStats();
  renderAnalytics();
  renderCalendar();
}

/* ── RENDER TASKS PAGE ──────────────────────────────────────────────────── */
function renderTasksPage() {
  const el = $('tasks-list');
  if (!el) return;
  const tab = state.filterTab;
  let filtered = state.tasks;
  if (tab === 'high') filtered = state.tasks.filter(t => (t.priority || '').toUpperCase() === 'HIGH');
  else if (tab === 'today') filtered = state.tasks.filter(t => t.deadline && /today/i.test(t.deadline));
  else if (tab === 'done') filtered = state.tasks.filter(t => t.done);

  $('tasks-count').textContent = `${filtered.length} task${filtered.length !== 1 ? 's' : ''}`;
  if (!filtered.length) {
    el.innerHTML = `<div class="empty-state"><div class="empty-icon">📋</div><p class="empty-title">No tasks here</p><p class="empty-sub">Try a different filter or add new tasks</p></div>`;
    return;
  }
  el.innerHTML = filtered.map((t, i) => taskItemHTML(t, state.tasks.indexOf(t))).join('');
}

/* ── STATS ──────────────────────────────────────────────────────────────── */
function updateStats() {
  const a = buildTaskAnalytics();
  ['stat-total', 'stat-total-t'].forEach(id => setText(id, a.total));
  ['stat-today', 'stat-today-t'].forEach(id => setText(id, a.today));
  ['stat-overdue', 'stat-overdue-t'].forEach(id => setText(id, a.overdue));
  ['stat-week', 'stat-week-t'].forEach(id => setText(id, a.week));
  setText('stat-rate', a.completion + '%');
  ['stat-streak', 'stat-streak-a'].forEach(id => setText(id, state.dashboard?.day_streak ?? 1));
  const bar = $('rate-bar');
  if (bar) bar.style.width = a.completion + '%';
  updateDonut(a.completion);
}

function setText(id, val) { const el = $(id); if (el) el.textContent = val; }

function updateDonut(pct) {
  pct = Number(pct) || 0;
  const fg = $('donut-fg');
  const txt = $('donut-text');
  if (!fg) return;
  const circ = 2 * Math.PI * 44;
  const fill = (pct / 100) * circ;
  fg.style.strokeDasharray = `${fill} ${circ}`;
  if (txt) txt.textContent = pct + '%';
}

function normalizeTask(t) {
  return {
    ...t,
    task: t.task || t.task_description || t.title || 'Untitled',
    deadline: t.deadline || t.due_date || '',
    deadline_iso: t.deadline_iso || t.due_date_iso || '',
    priority: String(t.priority || 'LOW').toUpperCase(),
    priority_reason: t.priority_reason || t.priorityReason || '',
    suggested_time: t.suggested_time || '',
    source: t.source || 'text',
    category: t.category || (t.source === 'email' ? 'Email' : 'General'),
  };
}

/* ── APPLY DATA FROM API ────────────────────────────────────────────────── */
function applyData(data) {
  if (data.tasks) {
    state.tasks = [...state.tasks, ...data.tasks.map(normalizeTask)];
    saveTasks();
  }
  if (!data.dashboard && data.dashboard_summary) {
    const s = data.dashboard_summary;
    data.dashboard = {
      welcome_message: `Imported ${(data.tasks || []).length} task(s) from Gmail.`,
      stats: [
        { label: 'Total Tasks', value: String(s.total_tasks ?? (data.tasks || []).length) },
        { label: 'Due Today', value: String(s.tasks_due_today ?? 0) },
        { label: 'Overdue', value: String(s.overdue_tasks ?? 0) },
        { label: 'This Week', value: String(s.tasks_due_this_week ?? 0) },
      ],
      day_streak: 1,
      task_completion_rate: 0,
      chart: defaultChart,
      insights: [],
    };
  }
  if (data.dashboard) {
    state.dashboard = data.dashboard;
    const d = data.dashboard;
    if (d.welcome_message) setText('ai-insight-text', d.welcome_message);
    if (d.day_streak !== undefined) setText('stat-streak', d.day_streak);
    if (d.insights) renderInsightsHome(d.insights);
    renderChart(buildTaskAnalytics().chart);
  }
  updateStats();
  renderHomeTaskPreview();
  renderTasksPage();
  renderCalendar();
  renderAnalytics();
}

/* ── TEXT EXTRACT ───────────────────────────────────────────────────────── */
async function extractText() {
  const text = $('text-input')?.value?.trim();
  if (!text) { toast('Please enter some text first', 'warning'); return; }
  const btn = $('extract-btn');
  setLoading(btn, true);
  try {
    const r = await fetch(`${API}/extract`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    const data = await r.json();
    if (!r.ok || !data.success) throw new Error(data.detail || 'Extraction failed');
    applyData(data);
    toast(`✅ Extracted ${(data.tasks || []).length} tasks!`, 'success');
    closeModal('add-modal');
    $('text-input').value = '';
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
  } finally {
    setLoading(btn, false);
  }
}

/* ── OCR EXTRACT ────────────────────────────────────────────────────────── */
async function extractOCR(file) {
  if (!file) return;
  const btn = $('ocr-btn');
  setLoading(btn, true);
  try {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch(`${API}/ocr`, { method: 'POST', body: fd });
    const data = await r.json();
    if (!r.ok || !data.success) throw new Error(data.detail || 'OCR failed');
    applyData(data);
    toast(`✅ OCR extracted ${(data.tasks || []).length} tasks!`, 'success');
    closeModal('add-modal');
  } catch (e) {
    toast(`OCR Error: ${e.message}`, 'error');
  } finally {
    setLoading(btn, false);
  }
}

/* ── GMAIL ──────────────────────────────────────────────────────────────── */
async function checkGmailStatus() {
  try {
    const r = await fetch(`${API}/email/status`);
    const d = await r.json();
    state.gmailConnected = d.connected;
    updateGmailUI();
  } catch (_) {}
}

function updateGmailUI() {
  const btn = $('gmail-btn');
  const status = $('gmail-status');
  if (!btn) return;
  if (state.gmailConnected) {
    btn.textContent = '✅ Connected';
    btn.classList.add('connected');
    if (status) status.textContent = 'Gmail connected — click Fetch to import tasks';
  } else {
    btn.textContent = '🔗 Connect Gmail';
    btn.classList.remove('connected');
    if (status) status.textContent = 'Connect your Gmail to auto-import tasks from emails';
  }
}

function connectGmail() {
  window.open(`${API}/email/auth`, '_blank', 'width=600,height=700');
  setTimeout(checkGmailStatus, 5000);
}

async function fetchGmailTasks() {
  if (!state.gmailConnected) { toast('Connect Gmail first', 'warning'); return; }
  const btn = $('gmail-fetch-btn');
  setLoading(btn, true);
  try {
    const r = await fetch(`${API}/email/fetch?max_emails=10`);
    const data = await r.json();
    if (!r.ok || !data.success) throw new Error(data.detail || 'Gmail fetch failed');
    applyData(data);
    toast(`✅ Imported ${(data.tasks || []).length} tasks from Gmail!`, 'success');
    closeModal('add-modal');
  } catch (e) {
    toast(`Gmail Error: ${e.message}`, 'error');
  } finally {
    setLoading(btn, false);
  }
}

/* ── REMINDER ───────────────────────────────────────────────────────────── */
function openReminderFor(taskIdx) {
  state.currentReminderTask = state.tasks[taskIdx];
  const t = state.currentReminderTask;
  setText('reminder-task-name', t?.task || t?.title || 'Task');
  openModal('reminder-modal');
}

async function sendReminder() {
  const phone = $('reminder-phone')?.value?.trim();
  const when = $('reminder-when')?.value;
  const t = state.currentReminderTask;
  if (!phone) { toast('Enter a phone number', 'warning'); return; }
  if (!phone.startsWith('+')) { toast('Use E.164 format: +919876543210', 'warning'); return; }
  const btn = $('reminder-send-btn');
  setLoading(btn, true);
  try {
    const body = {
      phone,
      task: t?.task || t?.title || 'Task',
      deadline: t?.deadline || '',
      remind_at: when || null,
    };
    const r = await fetch(`${API}/reminder/set`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok || !data.success) throw new Error(data.detail || 'Failed to set reminder');
    toast(data.scheduled ? `⏰ Reminder scheduled for ${when}` : '📱 Reminder sent!', 'success');
    closeModal('reminder-modal');
  } catch (e) {
    toast(`Reminder Error: ${e.message}`, 'error');
  } finally {
    setLoading(btn, false);
  }
}

/* ── LOADING STATE ──────────────────────────────────────────────────────── */
function setLoading(btn, isLoading) {
  if (!btn) return;
  btn.disabled = isLoading;
  if (isLoading) {
    btn._orig = btn.innerHTML;
    btn.innerHTML = `<span class="spinner"></span> Processing...`;
  } else {
    btn.innerHTML = btn._orig || btn.innerHTML;
  }
}

/* ── CHART ──────────────────────────────────────────────────────────────── */
function renderChart(chart, containerId = 'chart-bars') {
  const el = $(containerId);
  if (!el || !chart) return;
  const max = Math.max(...chart.map(c => Math.max(c.value || 0, c.completed || 0)), 1);
  el.innerHTML = chart.map(c => `
    <div class="chart-bar-group">
      <div class="chart-bar extracted" style="height:${Math.max(8, (c.value / max) * 100)}%"></div>
      <div class="chart-bar completed" style="height:${Math.max(4, ((c.completed || 0) / max) * 100)}%"></div>
      <span class="chart-label">${esc(c.label)}</span>
    </div>`).join('');
}

/* ── INSIGHTS ───────────────────────────────────────────────────────────── */
function renderInsightsHome(insights) {
  const el = $('insights-list');
  const analyticsEl = $('insights-list-a');
  if (!el) return;
  const colors = { amber: '#f59e0b', blue: '#3b82f6', green: '#10b981' };
  const icons = { amber: '⚡', blue: '🎯', green: '🏆' };
  el.innerHTML = (insights || []).map(i => `
    <div class="insight-card">
      <div class="insight-icon" style="background:${colors[i.tone] || '#10b981'}22">${icons[i.tone] || '✨'}</div>
      <div class="insight-content">
        <div class="insight-title">${esc(i.title)}</div>
        <div class="insight-detail">${esc(i.detail)}</div>
      </div>
    </div>`).join('');
  if (analyticsEl) analyticsEl.innerHTML = el.innerHTML;
}

/* ── CALENDAR ───────────────────────────────────────────────────────────── */
function renderCalendar(containerId, labelId) {
  const ids = containerId ? [containerId] : ['cal-grid', 'cal-grid-full'];
  const labels = labelId ? [labelId] : ['cal-month-label', 'cal-full-label'];
  ids.forEach((cid, idx) => {
    const grid = $(cid);
    const label = $(labels[idx]);
    if (!grid) return;
    const now = new Date();
    const d = new Date(now.getFullYear(), now.getMonth() + state.calOffset, 1);
    if (label) label.textContent = d.toLocaleDateString('en-IN', { month: 'long', year: 'numeric' });
    const firstDay = d.getDay();
    const daysInMonth = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
    grid.innerHTML = '';
    for (let i = 0; i < firstDay; i++) grid.innerHTML += `<div class="cal-day empty">0</div>`;
    for (let day = 1; day <= daysInMonth; day++) {
      const date = new Date(d.getFullYear(), d.getMonth(), day);
      const tasks = state.tasks.filter(t => sameDay(taskDate(t), date));
      const isToday = sameDay(date, now);
      const preview = tasks.slice(0, 2).map(t => `<span class="cal-task ${priorityFromTask(t).toLowerCase()}">${esc(t.task)}</span>`).join('');
      const more = tasks.length > 2 ? `<span class="cal-more">+${tasks.length - 2} more</span>` : '';
      grid.innerHTML += `<div class="cal-day${isToday ? ' today' : ''}${tasks.length ? ' has-task' : ''}" onclick="openCalendarDay('${dateKey(date)}')" title="${esc(tasks.map(t => t.task).join(', '))}">
        <span class="cal-num">${day}</span>${preview}${more}
      </div>`;
    }
  });
}

function shiftMonth(delta) { state.calOffset += delta; renderCalendar(); }

function openCalendarDay(key) {
  const date = new Date(`${key}T00:00:00`);
  const tasks = state.tasks.filter(t => sameDay(taskDate(t), date));
  const title = $('calendar-modal-title');
  const body = $('calendar-modal-body');
  if (title) title.textContent = date.toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  if (body) {
    body.innerHTML = tasks.length ? tasks.map(t => `
      <div class="calendar-task-row">
        <div>
          <div class="task-title">${esc(t.task)}</div>
          <div class="task-meta">
            ${priorityBadge(priorityFromTask(t))}
            ${t.priority_reason ? `<span style="font-size:12px;color:var(--muted)">AI: ${esc(t.priority_reason)}</span>` : ''}
            ${sourceBadge(t.source)}
            ${t.deadline ? `<span style="font-size:12px;color:var(--muted)">${esc(t.deadline)}</span>` : ''}
          </div>
        </div>
      </div>
    `).join('') : `<div class="empty-state"><div class="empty-icon">📅</div><p class="empty-title">No tasks due</p><p class="empty-sub">This date has no synced task deadlines.</p></div>`;
  }
  openModal('calendar-day-modal');
}

/* ── ANALYTICS PAGE ─────────────────────────────────────────────────────── */
function renderAnalytics() {
  const a = buildTaskAnalytics();
  const chart = a.chart.some(c => c.value || c.completed) ? a.chart : defaultChart;
  renderChart(chart, 'analytics-chart');
  renderChart(chart, 'chart-bars');
  updateDonut(a.completion);
  renderInsightsHome(state.dashboard?.insights || defaultInsights);
}

/* ── DROPZONE ───────────────────────────────────────────────────────────── */
function initDropzone() {
  const zone = $('dropzone');
  if (!zone) return;
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file) extractOCR(file);
  });
  zone.addEventListener('click', () => $('ocr-file-input')?.click());
}

/* ── DEFAULTS ───────────────────────────────────────────────────────────── */
const defaultChart = [
  { label: 'Mon', value: 4 }, { label: 'Tue', value: 6 }, { label: 'Wed', value: 5 },
  { label: 'Thu', value: 8 }, { label: 'Fri', value: 6 }, { label: 'Sat', value: 3 },
  { label: 'Sun', value: 5 },
];
const defaultInsights = [
  { title: 'Getting Started', detail: 'Add your first task using the + Add Task button above.', tone: 'amber' },
  { title: 'Try OCR', detail: 'Upload a screenshot of any to-do list to extract tasks instantly.', tone: 'blue' },
  { title: 'Enable Reminders', detail: 'Set SMS reminders on tasks so you never miss a deadline.', tone: 'green' },
];

/* ── EXAMPLE CHIPS ──────────────────────────────────────────────────────── */
const examples = [
  "Need to finish React assignment by tomorrow night, call mom on Sunday, and prepare presentation for Monday 9AM meeting.",
  "Submit ML project by Friday 5PM, buy groceries today, review PR on GitHub, email prof about project extension.",
  "Gym at 7AM daily, complete tax filing by end of month, book flight tickets for next week.",
];
function loadExample(i) {
  const inp = $('text-input');
  if (inp) { inp.value = examples[i]; inp.focus(); }
}

/* ── LOAD DASHBOARD ─────────────────────────────────────────────────────── */
async function loadDashboard() {
  loadSavedTasks();
  try {
    const r = await fetch(`${API}/dashboard`);
    const data = await r.json();
    if (data.success && data.dashboard) {
      state.dashboard = data.dashboard;
      setText('ai-insight-text', data.dashboard.welcome_message || '');
    }
  } catch (_) {}
  renderChart(buildTaskAnalytics().chart.some(c => c.value) ? buildTaskAnalytics().chart : defaultChart);
  renderInsightsHome(defaultInsights);
  updateStats();
  renderHomeTaskPreview();
  renderTasksPage();
  renderCalendar();
}

/* ── CHECK URL PARAMS (gmail callback) ──────────────────────────────────── */
function checkUrlParams() {
  const params = new URLSearchParams(window.location.search);
  if (params.get('gmail') === 'connected') {
    state.gmailConnected = true;
    updateGmailUI();
    toast('✅ Gmail connected successfully!', 'success');
    window.history.replaceState({}, '', window.location.pathname);
  }
}

/* ── WIRE EVENTS ────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  requireAuth();
  initTheme();
  updateUserUI();
  checkUrlParams();
  loadDashboard();
  checkGmailStatus();
  initDropzone();
  showPage('home');

  // Nav
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => showPage(btn.dataset.page));
  });

  // Modals close on backdrop click
  document.querySelectorAll('.modal-overlay').forEach(m => {
    m.addEventListener('click', e => { if (e.target === m) closeModal(m.id); });
  });

  // Modal tabs
  document.querySelectorAll('.modal-tab').forEach(t => {
    t.addEventListener('click', () => switchModalTab(t.dataset.tab));
  });

  // OCR file input
  const ocrInput = $('ocr-file-input');
  if (ocrInput) ocrInput.addEventListener('change', e => extractOCR(e.target.files[0]));

  // Filter tabs
  document.querySelectorAll('.filter-tab').forEach(t => {
    t.addEventListener('click', () => {
      document.querySelectorAll('.filter-tab').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      state.filterTab = t.dataset.filter;
      renderTasksPage();
    });
  });
});
