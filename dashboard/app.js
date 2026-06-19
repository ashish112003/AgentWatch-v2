// /**
//  * app.js — AgentWatch Dashboard
//  *
//  * Architecture:
//  *   State → API → Render pipeline.
//  *   No framework, no bundler.  Vanilla ES2022 modules pattern
//  *   (everything in one file for simplicity — split if the project grows).
//  *
//  * Sections:
//  *   1. State management
//  *   2. API client (all fetch calls, token injection)
//  *   3. Navigation / view router
//  *   4. Auth flow (register / paste token)
//  *   5. Render: Overview (stats + latency)
//  *   6. Render: Violations table
//  *   7. Render: Audit log table
//  *   8. Utilities (badges, timestamps, toast)
//  *   9. Auto-refresh + init
//  */

// 'use strict';

// /* ═══════════════════════════════════════════════════════════════
//    1. STATE
// ═══════════════════════════════════════════════════════════════ */

// const state = {
//   token:           null,       // JWT bearer token
//   agentId:         null,       // UUID of the authenticated agent
//   agentName:       null,
//   lastRefresh:     null,
//   refreshTimer:    null,
//   currentView:     'overview',
//   loading: {
//     stats:      false,
//     violations: false,
//     audit:      false,
//   },
//   data: {
//     stats:      null,
//     violations: null,    // { violations: [], total, skip, limit }
//     audit:      null,    // { events: [], total, skip, limit }
//   },
//   filters: {
//     auditEventType: '',
//     violationPage:  0,
//     auditPage:      0,
//     pageSize:       25,
//   },
// };

// /* ═══════════════════════════════════════════════════════════════
//    2. API CLIENT
// ═══════════════════════════════════════════════════════════════ */

// const API_BASE = '';  // Same origin — served by FastAPI at /dashboard/

// function authHeaders() {
//   return {
//     'Content-Type': 'application/json',
//     ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
//   };
// }

// /**
//  * Generic fetch wrapper.
//  * Returns { ok: true, data } or { ok: false, error, status }.
//  */
// async function apiFetch(path, opts = {}) {
//   try {
//     const res = await fetch(`${API_BASE}${path}`, {
//       ...opts,
//       headers: { ...authHeaders(), ...(opts.headers || {}) },
//     });
//     const data = await res.json().catch(() => null);
//     if (!res.ok) {
//       return { ok: false, error: data?.detail || `HTTP ${res.status}`, status: res.status };
//     }
//     return { ok: true, data };
//   } catch (err) {
//     return { ok: false, error: err.message || 'Network error', status: 0 };
//   }
// }

// async function fetchStats() {
//   return apiFetch('/analytics/stats');
// }

// async function fetchViolations(skip = 0, limit = 25) {
//   return apiFetch(`/governance/violations?skip=${skip}&limit=${limit}`);
// }

// async function fetchAuditLogs(skip = 0, limit = 25, eventType = '') {
//   const et = eventType ? `&event_type=${encodeURIComponent(eventType)}` : '';
//   return apiFetch(`/audit/logs?skip=${skip}&limit=${limit}${et}`);
// }

// async function registerAgent(name, secret, tools) {
//   return apiFetch('/agents/register', {
//     method: 'POST',
//     body: JSON.stringify({ name, secret, allowed_tools: tools }),
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    3. NAVIGATION
// ═══════════════════════════════════════════════════════════════ */

// function navigateTo(viewId) {
//   state.currentView = viewId;

//   // Update sidebar active state
//   document.querySelectorAll('.nav-item').forEach(el => {
//     el.classList.toggle('active', el.dataset.view === viewId);
//   });

//   // Swap visible view
//   document.querySelectorAll('.view').forEach(el => {
//     el.classList.toggle('active', el.id === `view-${viewId}`);
//   });

//   // Update topbar title
//   const titles = {
//     overview:   { title: 'Overview',           sub: 'Platform health at a glance' },
//     violations: { title: 'Governance Violations', sub: 'Blocked tool calls and policy enforcement' },
//     audit:      { title: 'Audit Log',           sub: 'Full event trace across all agent runs' },
//   };
//   const t = titles[viewId] || {};
//   document.getElementById('topbar-title').textContent    = t.title || '';
//   document.getElementById('topbar-subtitle').textContent = t.sub   || '';

//   // Load data for the view
//   if (!state.token) return;
//   if (viewId === 'overview')   refreshOverview();
//   if (viewId === 'violations') refreshViolations();
//   if (viewId === 'audit')      refreshAuditLog();
// }

// /* ═══════════════════════════════════════════════════════════════
//    4. AUTH FLOW
// ═══════════════════════════════════════════════════════════════ */

// function setToken(token, agentId, agentName) {
//   state.token     = token;
//   state.agentId   = agentId;
//   state.agentName = agentName || 'unknown';

//   // Persist in sessionStorage (not localStorage — clears on tab close)
//   sessionStorage.setItem('aw_token',      token);
//   sessionStorage.setItem('aw_agent_id',   agentId);
//   sessionStorage.setItem('aw_agent_name', agentName || '');

//   updateConnectionStatus('connected', agentName || agentId?.slice(0, 8) + '…');
//   hideAuthBanner();
//   startAutoRefresh();
//   navigateTo(state.currentView);
// }

// function clearToken() {
//   state.token = state.agentId = state.agentName = null;
//   sessionStorage.removeItem('aw_token');
//   sessionStorage.removeItem('aw_agent_id');
//   sessionStorage.removeItem('aw_agent_name');
//   stopAutoRefresh();
//   updateConnectionStatus('disconnected', 'Not connected');
//   showAuthBanner();
// }

// function tryRestoreSession() {
//   const token     = sessionStorage.getItem('aw_token');
//   const agentId   = sessionStorage.getItem('aw_agent_id');
//   const agentName = sessionStorage.getItem('aw_agent_name');
//   if (token && agentId) {
//     setToken(token, agentId, agentName);
//     return true;
//   }
//   return false;
// }

// function showAuthBanner() {
//   document.getElementById('auth-banner').style.display = 'block';
// }

// function hideAuthBanner() {
//   document.getElementById('auth-banner').style.display = 'none';
// }

// async function handleRegister() {
//   const name   = document.getElementById('reg-name').value.trim();
//   const secret = document.getElementById('reg-secret').value;
//   const tools  = Array.from(
//     document.querySelectorAll('.tool-checkbox:checked')
//   ).map(el => el.value);

//   if (!name || name.length < 2) {
//     return toast('Agent name must be at least 2 characters', 'error');
//   }
//   if (!secret || secret.length < 12) {
//     return toast('Secret must be at least 12 characters', 'error');
//   }

//   const btn = document.getElementById('btn-register');
//   btn.disabled = true;
//   btn.innerHTML = '<span class="btn-icon btn-spinning">⟳</span> Registering…';

//   const { ok, data, error } = await registerAgent(name, secret, tools);

//   btn.disabled = false;
//   btn.innerHTML = '🚀 Register Agent';

//   if (!ok) {
//     toast(error || 'Registration failed', 'error');
//     return;
//   }

//   toast(`Agent "${name}" registered successfully!`, 'success');
//   setToken(data.access_token, data.agent_id, name);
//   document.getElementById('token-display').value = data.access_token;
// }

// async function handlePasteToken() {
//   const raw = document.getElementById('token-display').value.trim();
//   if (!raw) return toast('Paste a JWT token first', 'error');

//   // Decode payload to extract sub (agent_id) and agent_name — no library needed
//   try {
//     const parts = raw.split('.');
//     if (parts.length !== 3) throw new Error('Not a JWT');
//     const payload = JSON.parse(atob(parts[1].replace(/-/g,'+').replace(/_/g,'/')));
//     if (!payload.sub) throw new Error('Token has no sub claim');
//     setToken(raw, payload.sub, payload.agent_name || payload.sub.slice(0, 8) + '…');
//     toast('Token applied successfully', 'success');
//   } catch (e) {
//     toast(`Invalid token: ${e.message}`, 'error');
//   }
// }

// /* ═══════════════════════════════════════════════════════════════
//    5. OVERVIEW — stats cards + latency bars
// ═══════════════════════════════════════════════════════════════ */

// async function refreshOverview() {
//   if (state.loading.stats) return;
//   state.loading.stats = true;

//   // Show skeleton values while loading
//   renderStatSkeletons();

//   const { ok, data, error } = await fetchStats();
//   state.loading.stats = false;

//   if (!ok) {
//     toast(error || 'Failed to load stats', 'error');
//     renderStatsError();
//     return;
//   }

//   state.data.stats = data;
//   renderStats(data);
//   renderLatency(data.tool_latency || []);
//   const vRes = await fetchViolations(0, 5);
//   if (vRes.ok) renderOverviewViolations(vRes.data);
// }


// function renderStatSkeletons() {
//   const ids = ['stat-agents','stat-runs','stat-events','stat-violations','stat-rate','stat-completed'];
//   ids.forEach(id => {
//     const el = document.getElementById(id);
//     if (el) el.innerHTML = '<span class="skeleton" style="display:inline-block;width:48px;height:28px;"></span>';
//   });
// }

// function renderStatsError() {
//   const ids = ['stat-agents','stat-runs','stat-events','stat-violations','stat-rate','stat-completed'];
//   ids.forEach(id => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = '—';
//   });
// }

// function renderStats(s) {
//   const set = (id, val) => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = val;
//   };

//   set('stat-agents',    s.total_agents);
//   set('stat-runs',      s.total_runs);
//   set('stat-events',    s.total_events);
//   set('stat-violations', s.total_violations);
//   set('stat-rate',      `${s.violation_rate.toFixed(1)}%`);
//   set('stat-completed', s.completed_runs);

//   // Sub-lines
//   const setSub = (id, val) => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = val;
//   };
//   setSub('stat-runs-sub',       `${s.completed_runs} completed · ${s.failed_runs} failed`);
//   setSub('stat-violations-sub', s.total_tool_calls > 0
//     ? `out of ${s.total_tool_calls} tool calls`
//     : 'no tool calls yet');
// }

// function renderLatency(tools) {
//   const grid = document.getElementById('latency-grid');
//   if (!grid) return;

//   if (!tools || tools.length === 0) {
//     grid.innerHTML = `
//       <div class="empty-state" style="padding:24px; grid-column:1/-1;">
//         <span class="empty-icon">⏱</span>
//         <span class="empty-title">No tool executions yet</span>
//         <span class="empty-body">Run an agent with a prompt to see latency data here.</span>
//       </div>`;
//     return;
//   }

//   const maxMs = Math.max(...tools.map(t => t.p95_ms || t.avg_ms || 1), 1);

//   grid.innerHTML = tools.map(t => {
//     const avgPct = Math.min(100, ((t.avg_ms || 0) / maxMs) * 100).toFixed(1);
//     const p95Pct = Math.min(100, ((t.p95_ms || 0) / maxMs) * 100).toFixed(1);

//     return `
//       <div class="latency-card">
//         <div class="latency-header">
//           <span class="latency-tool-name">${escHtml(t.tool_name)}</span>
//           <span class="latency-calls">${t.call_count} calls</span>
//         </div>
//         <div class="latency-bar-row">
//           <span class="latency-bar-label">avg</span>
//           <div class="latency-bar-track">
//             <div class="latency-bar-fill" style="width:${avgPct}%"></div>
//           </div>
//           <span class="latency-val">${t.avg_ms != null ? t.avg_ms.toFixed(1) + 'ms' : '—'}</span>
//         </div>
//         <div class="latency-bar-row">
//           <span class="latency-bar-label">p95</span>
//           <div class="latency-bar-track">
//             <div class="latency-bar-fill p95" style="width:${p95Pct}%"></div>
//           </div>
//           <span class="latency-val">${t.p95_ms != null ? t.p95_ms.toFixed(1) + 'ms' : '—'}</span>
//         </div>
//       </div>`;
//   }).join('');
// }


// // function renderOverviewViolations(data) {
// //   const tbody   = document.getElementById('overview-violations-tbody');
// //   const counter = document.getElementById('overview-violations-count');
// //   // ... renders up to 5 rows into the overview panel only
// //   // Never touches #violations-tbody or calls renderViolations()
// // }
// function renderOverviewViolations(data) {
//   const tbody   = document.getElementById('overview-violations-tbody');
//   const counter = document.getElementById('overview-violations-count');
//   if (!tbody) return;

//   if (counter) counter.textContent = data.total;

//   if (!data.violations || data.violations.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="5">
//         <div class="empty-state" style="padding:20px;">
//           <span class="empty-icon">✅</span>
//           <span class="empty-title">No violations yet</span>
//         </div>
//       </td></tr>`;
//     return;
//   }

//   tbody.innerHTML = data.violations.slice(0, 5).map(v => {
//     const inputStr = v.attempted_input
//       ? JSON.stringify(v.attempted_input).slice(0, 50)
//       : '—';
//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(v.timestamp)}</td>
//         <td>${escHtml(v.agent_name || '—')}</td>
//         <td><span class="badge badge-tool">${escHtml(v.tool_name || '—')}</span></td>
//         <td>
//           <span class="violation-input"
//                 style="font-family:var(--font-mono);font-size:11.5px;">
//             ${escHtml(inputStr)}
//           </span>
//         </td>
//         <td style="font-size:12px;color:var(--text-secondary);
//                    max-width:180px;overflow:hidden;
//                    text-overflow:ellipsis;white-space:nowrap;">
//           ${escHtml((v.denial_message || '').slice(0, 60))}
//         </td>
//       </tr>`;
//   }).join('');
// }

// /* ═══════════════════════════════════════════════════════════════
//    6. VIOLATIONS TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshViolations() {
//   if (state.loading.violations) return;
//   state.loading.violations = true;
//   setTableLoading('violations-tbody');

//   const skip  = state.filters.violationPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchViolations(skip, state.filters.pageSize);
//   state.loading.violations = false;

//   if (!ok) {
//     toast(error || 'Failed to load violations', 'error');
//     return;
//   }

//   state.data.violations = data;
//   renderViolations(data);
// }

// function renderViolations(data) {
//   const tbody   = document.getElementById('violations-tbody');
//   const counter = document.getElementById('violations-count');
//   if (counter) counter.textContent = data.total;

//   if (!data.violations || data.violations.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="6">
//         <div class="empty-state">
//           <span class="empty-icon">✅</span>
//           <span class="empty-title">No governance violations</span>
//           <span class="empty-body">All tool calls are within permitted bounds. Register an agent with limited tools and run a prompt to see violations here.</span>
//         </div>
//       </td></tr>`;
//     renderPagination('violations-pagination', data, state.filters.violationPage, (p) => {
//       state.filters.violationPage = p;
//       refreshViolations();
//     });
//     return;
//   }

//   tbody.innerHTML = data.violations.map(v => {
//     const inputStr = v.attempted_input
//       ? JSON.stringify(v.attempted_input)
//       : '—';

//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(v.timestamp)}</td>
//         <td>
//           <span class="badge badge-violation">⛔ violation</span>
//         </td>
//         <td>
//           <span style="font-weight:500;">${escHtml(v.agent_name || '—')}</span>
//           <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(v.agent_id.slice(0,8))}…</span>
//         </td>
//         <td><span class="badge badge-tool">${escHtml(v.tool_name || '—')}</span></td>
//         <td>
//           <div class="violation-detail">
//             <span class="violation-input">${escHtml(inputStr)}</span>
//           </div>
//         </td>
//         <td>
//           <div style="max-width:220px; font-size:12px; color:var(--text-secondary); line-height:1.5;">
//             ${escHtml(v.denial_message || '—')}
//           </div>
//         </td>
//       </tr>`;
//   }).join('');

//   renderPagination('violations-pagination', data, state.filters.violationPage, (p) => {
//     state.filters.violationPage = p;
//     refreshViolations();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    7. AUDIT LOG TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshAuditLog() {
//   if (state.loading.audit) return;
//   state.loading.audit = true;
//   setTableLoading('audit-tbody');

//   const skip = state.filters.auditPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchAuditLogs(
//     skip,
//     state.filters.pageSize,
//     state.filters.auditEventType,
//   );
//   state.loading.audit = false;

//   if (!ok) {
//     toast(error || 'Failed to load audit log', 'error');
//     return;
//   }

//   state.data.audit = data;
//   renderAuditLog(data);
// }

// function renderAuditLog(data) {
//   const tbody   = document.getElementById('audit-tbody');
//   const counter = document.getElementById('audit-count');
//   if (counter) counter.textContent = data.total;

//   if (!data.events || data.events.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="7">
//         <div class="empty-state">
//           <span class="empty-icon">📋</span>
//           <span class="empty-title">No audit events</span>
//           <span class="empty-body">Submit a run via POST /runs to populate the audit log.</span>
//         </div>
//       </td></tr>`;
//     renderPagination('audit-pagination', data, state.filters.auditPage, (p) => {
//       state.filters.auditPage = p;
//       refreshAuditLog();
//     });
//     return;
//   }

//   tbody.innerHTML = data.events.map(e => {
//     const inputSummary = e.input_data
//       ? JSON.stringify(e.input_data).slice(0, 60) + (JSON.stringify(e.input_data).length > 60 ? '…' : '')
//       : '—';

//     const outputSummary = e.output_data
//       ? JSON.stringify(e.output_data).slice(0, 60) + (JSON.stringify(e.output_data).length > 60 ? '…' : '')
//       : '—';

//     const permittedBadge = e.permitted === true
//       ? '<span class="badge badge-permitted">✓ allowed</span>'
//       : e.permitted === false
//         ? '<span class="badge badge-blocked">⛔ blocked</span>'
//         : '<span style="color:var(--text-muted);font-size:12px;">—</span>';

//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(e.timestamp)}</td>
//         <td>${eventTypeBadge(e.event_type)}</td>
//         <td>
//           <span style="font-weight:500;">${escHtml(e.agent_name || '—')}</span>
//         </td>
//         <td>
//           ${e.tool_name
//             ? `<span class="badge badge-tool">${escHtml(e.tool_name)}</span>`
//             : '<span class="td-dim">—</span>'
//           }
//         </td>
//         <td>${permittedBadge}</td>
//         <td>
//           <div class="td-truncate td-mono" style="font-size:11.5px; color:var(--text-secondary);">
//             ${escHtml(inputSummary)}
//           </div>
//         </td>
//         <td>
//           ${e.latency_ms != null
//             ? `<span class="td-mono" style="font-size:12px; color:var(--accent-teal);">${e.latency_ms.toFixed(1)}ms</span>`
//             : '<span class="td-dim">—</span>'
//           }
//         </td>
//       </tr>`;
//   }).join('');

//   renderPagination('audit-pagination', data, state.filters.auditPage, (p) => {
//     state.filters.auditPage = p;
//     refreshAuditLog();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    8. UTILITIES
// ═══════════════════════════════════════════════════════════════ */

// function escHtml(str) {
//   if (str == null) return '';
//   return String(str)
//     .replace(/&/g, '&amp;')
//     .replace(/</g, '&lt;')
//     .replace(/>/g, '&gt;')
//     .replace(/"/g, '&quot;');
// }

// function relativeTime(isoString) {
//   if (!isoString) return '—';
//   const date  = new Date(isoString.endsWith('Z') ? isoString : isoString + 'Z');
//   const diff  = (Date.now() - date.getTime()) / 1000;
//   if (diff < 5)   return 'just now';
//   if (diff < 60)  return `${Math.floor(diff)}s ago`;
//   if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
//   if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
//   return date.toLocaleDateString();
// }

// function eventTypeBadge(type) {
//   const map = {
//     'run_start': '<span class="badge badge-run-start">▶ run_start</span>',
//     'tool_call': '<span class="badge badge-tool-call">⚙ tool_call</span>',
//     'tool_end':  '<span class="badge badge-tool-end">✓ tool_end</span>',
//     'violation': '<span class="badge badge-violation">⛔ violation</span>',
//     'run_end':   '<span class="badge badge-run-end">■ run_end</span>',
//   };
//   return map[type] || `<span class="badge">${escHtml(type)}</span>`;
// }

// function setTableLoading(tbodyId) {
//   const el = document.getElementById(tbodyId);
//   if (!el) return;
//   el.innerHTML = Array.from({ length: 5 }, () => `
//     <tr>${Array.from({ length: 7 }, () =>
//       `<td><div class="skeleton" style="height:16px; width:${60 + Math.random()*60 | 0}px;"></div></td>`
//     ).join('')}</tr>
//   `).join('');
// }

// function renderPagination(containerId, data, currentPage, onPage) {
//   const el = document.getElementById(containerId);
//   if (!el) return;

//   const totalPages = Math.ceil(data.total / (data.limit || 25));
//   if (totalPages <= 1) { el.innerHTML = ''; return; }

//   const prevDisabled = currentPage === 0;
//   const nextDisabled = currentPage >= totalPages - 1;

//   el.innerHTML = `
//     <div style="display:flex; align-items:center; gap:10px; justify-content:flex-end; padding:12px 14px; border-top:1px solid var(--border);">
//       <span style="font-size:12px; color:var(--text-muted);">
//         ${data.skip + 1}–${Math.min(data.skip + data.limit, data.total)} of ${data.total}
//       </span>
//       <button class="btn btn-ghost btn-sm" ${prevDisabled ? 'disabled' : ''} onclick="(${onPage.toString()})(${currentPage - 1})">
//         ← Prev
//       </button>
//       <button class="btn btn-ghost btn-sm" ${nextDisabled ? 'disabled' : ''} onclick="(${onPage.toString()})(${currentPage + 1})">
//         Next →
//       </button>
//     </div>`;
// }

// function updateConnectionStatus(status, label) {
//   const dot  = document.getElementById('status-dot');
//   const text = document.getElementById('status-text');
//   if (dot)  { dot.className = `status-dot ${status}`; }
//   if (text) { text.textContent = label || status; }
// }

// function updateLastRefresh() {
//   state.lastRefresh = new Date();
//   const el = document.getElementById('last-refresh');
//   if (el) el.textContent = `Refreshed ${relativeTime(state.lastRefresh.toISOString())}`;
// }

// let _refreshLabelTimer = null;
// function startRefreshLabelUpdater() {
//   clearInterval(_refreshLabelTimer);
//   _refreshLabelTimer = setInterval(() => {
//     if (!state.lastRefresh) return;
//     const el = document.getElementById('last-refresh');
//     if (el) el.textContent = `Refreshed ${relativeTime(state.lastRefresh.toISOString())}`;
//   }, 15_000);
// }

// /* Toast notifications */
// function toast(message, type = 'info') {
//   const container = document.getElementById('toast-container');
//   if (!container) return;

//   const icons = { error: '⚠', success: '✓', info: 'ℹ' };
//   const el    = document.createElement('div');
//   el.className = `toast ${type}`;
//   el.innerHTML = `<span>${icons[type] || icons.info}</span><span>${escHtml(message)}</span>`;
//   container.appendChild(el);

//   setTimeout(() => {
//     el.style.animation = 'toast-out 0.2s ease forwards';
//     setTimeout(() => el.remove(), 200);
//   }, 3500);
// }

// /* ═══════════════════════════════════════════════════════════════
//    9. AUTO-REFRESH + INIT
// ═══════════════════════════════════════════════════════════════ */

// const REFRESH_INTERVAL_MS = 30_000;

// function refreshCurrentView() {
//   if (!state.token) return;

//   const btn = document.getElementById('btn-refresh');
//   if (btn) {
//     btn.classList.add('btn-spinning');
//     btn.querySelector('.btn-icon').style.animation = 'spin 0.8s linear infinite';
//   }

//   const views = {
//     overview:   refreshOverview,
//     violations: refreshViolations,
//     audit:      refreshAuditLog,
//   };
//   const fn = views[state.currentView];
//   if (fn) fn().finally(() => {
//     updateLastRefresh();
//     if (btn) btn.querySelector('.btn-icon').style.animation = '';
//   });
// }

// function startAutoRefresh() {
//   stopAutoRefresh();
//   state.refreshTimer = setInterval(refreshCurrentView, REFRESH_INTERVAL_MS);
// }

// function stopAutoRefresh() {
//   if (state.refreshTimer) clearInterval(state.refreshTimer);
//   state.refreshTimer = null;
// }

// /* ── Boot ─────────────────────────────────────────────────────── */
// document.addEventListener('DOMContentLoaded', () => {

//   // Nav click handlers
//   document.querySelectorAll('.nav-item[data-view]').forEach(el => {
//     el.addEventListener('click', () => navigateTo(el.dataset.view));
//   });

//   // Refresh button
//   document.getElementById('btn-refresh')?.addEventListener('click', refreshCurrentView);

//   // Disconnect button
//   document.getElementById('btn-disconnect')?.addEventListener('click', () => {
//     clearToken();
//     toast('Disconnected', 'info');
//   });

//   // Register form
//   document.getElementById('btn-register')?.addEventListener('click', handleRegister);

//   // Paste token
//   document.getElementById('btn-use-token')?.addEventListener('click', handlePasteToken);

//   // Audit event type filter
//   document.getElementById('audit-filter-type')?.addEventListener('change', (e) => {
//     state.filters.auditEventType = e.target.value;
//     state.filters.auditPage      = 0;
//     refreshAuditLog();
//   });

//   // Mobile sidebar toggle
//   document.getElementById('mobile-menu-btn')?.addEventListener('click', () => {
//     document.querySelector('.sidebar')?.classList.toggle('open');
//   });

//   // Start relative-time label updater
//   startRefreshLabelUpdater();

//   // Restore session or show auth banner
//   if (!tryRestoreSession()) {
//     showAuthBanner();
//     updateConnectionStatus('disconnected', 'Not connected');
//   }
// });


/**
 * app.js — AgentWatch Dashboard
 *
 * Sections:
 *   1. State management
 *   2. API client
 *   3. Navigation
 *   4. Auth flow
 *   5. Overview (stats + latency)
 *   5b. Overview violations preview
 *   6. Violations table
 *   7. Audit log table
 *   7b. Agent interactions table
 *   8. Utilities
 *   9. Auto-refresh + init
 */

// 'use strict';

// /* ═══════════════════════════════════════════════════════════════
//    1. STATE
// ═══════════════════════════════════════════════════════════════ */

// const state = {
//   token:       null,
//   agentId:     null,
//   agentName:   null,
//   lastRefresh: null,
//   refreshTimer: null,
//   currentView: 'overview',
//   loading: {
//     stats:        false,
//     violations:   false,
//     audit:        false,
//     interactions: false,
//   },
//   data: {
//     stats:        null,
//     violations:   null,
//     audit:        null,
//     interactions: null,
//   },
//   filters: {
//     auditEventType:   '',
//     violationPage:    0,
//     auditPage:        0,
//     interactionsPage: 0,
//     pageSize:         25,
//   },
// };

// /* ═══════════════════════════════════════════════════════════════
//    2. API CLIENT
// ═══════════════════════════════════════════════════════════════ */

// const API_BASE = '';

// function authHeaders() {
//   return {
//     'Content-Type': 'application/json',
//     ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
//   };
// }

// async function apiFetch(path, opts = {}) {
//   try {
//     const res = await fetch(`${API_BASE}${path}`, {
//       ...opts,
//       headers: { ...authHeaders(), ...(opts.headers || {}) },
//     });
//     const data = await res.json().catch(() => null);
//     if (!res.ok) {
//       return { ok: false, error: data?.detail || `HTTP ${res.status}`, status: res.status };
//     }
//     return { ok: true, data };
//   } catch (err) {
//     return { ok: false, error: err.message || 'Network error', status: 0 };
//   }
// }

// async function fetchStats() {
//   return apiFetch('/analytics/stats');
// }

// async function fetchViolations(skip = 0, limit = 25) {
//   return apiFetch(`/governance/violations?skip=${skip}&limit=${limit}`);
// }

// async function fetchInteractions(skip = 0, limit = 25) {
//   return apiFetch(`/agent-interactions?skip=${skip}&limit=${limit}`);
// }

// async function fetchAuditLogs(skip = 0, limit = 25, eventType = '') {
//   const et = eventType ? `&event_type=${encodeURIComponent(eventType)}` : '';
//   return apiFetch(`/audit/logs?skip=${skip}&limit=${limit}${et}`);
// }

// async function registerAgent(name, secret, tools) {
//   return apiFetch('/agents/register', {
//     method: 'POST',
//     body: JSON.stringify({ name, secret, allowed_tools: tools }),
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    3. NAVIGATION
// ═══════════════════════════════════════════════════════════════ */

// function navigateTo(viewId) {
//   state.currentView = viewId;

//   document.querySelectorAll('.nav-item').forEach(el => {
//     el.classList.toggle('active', el.dataset.view === viewId);
//   });

//   document.querySelectorAll('.view').forEach(el => {
//     el.classList.toggle('active', el.id === `view-${viewId}`);
//   });

//   const titles = {
//     overview:     { title: 'Overview',              sub: 'Platform health at a glance' },
//     violations:   { title: 'Governance Violations', sub: 'Blocked tool calls and policy enforcement' },
//     audit:        { title: 'Audit Log',             sub: 'Full event trace across all agent runs' },
//     interactions: { title: 'Agent Interactions',    sub: 'Agent-to-agent communication records' },
//   };
//   const t = titles[viewId] || {};
//   document.getElementById('topbar-title').textContent    = t.title || '';
//   document.getElementById('topbar-subtitle').textContent = t.sub   || '';

//   if (!state.token) return;
//   if (viewId === 'overview')     refreshOverview();
//   if (viewId === 'violations')   refreshViolations();
//   if (viewId === 'audit')        refreshAuditLog();
//   if (viewId === 'interactions') refreshInteractions();
// }

// /* ═══════════════════════════════════════════════════════════════
//    4. AUTH FLOW
// ═══════════════════════════════════════════════════════════════ */

// function setToken(token, agentId, agentName) {
//   state.token     = token;
//   state.agentId   = agentId;
//   state.agentName = agentName || 'unknown';

//   sessionStorage.setItem('aw_token',      token);
//   sessionStorage.setItem('aw_agent_id',   agentId);
//   sessionStorage.setItem('aw_agent_name', agentName || '');

//   updateConnectionStatus('connected', agentName || agentId?.slice(0, 8) + '…');
//   hideAuthBanner();
//   startAutoRefresh();
//   navigateTo(state.currentView);
// }

// function clearToken() {
//   state.token = state.agentId = state.agentName = null;
//   sessionStorage.removeItem('aw_token');
//   sessionStorage.removeItem('aw_agent_id');
//   sessionStorage.removeItem('aw_agent_name');
//   stopAutoRefresh();
//   updateConnectionStatus('disconnected', 'Not connected');
//   showAuthBanner();
// }

// function tryRestoreSession() {
//   const token     = sessionStorage.getItem('aw_token');
//   const agentId   = sessionStorage.getItem('aw_agent_id');
//   const agentName = sessionStorage.getItem('aw_agent_name');
//   if (token && agentId) {
//     setToken(token, agentId, agentName);
//     return true;
//   }
//   return false;
// }

// function showAuthBanner() {
//   document.getElementById('auth-banner').style.display = 'block';
// }

// function hideAuthBanner() {
//   document.getElementById('auth-banner').style.display = 'none';
// }

// async function handleRegister() {
//   const name   = document.getElementById('reg-name').value.trim();
//   const secret = document.getElementById('reg-secret').value;
//   const tools  = Array.from(
//     document.querySelectorAll('.tool-checkbox:checked')
//   ).map(el => el.value);

//   if (!name || name.length < 2) {
//     return toast('Agent name must be at least 2 characters', 'error');
//   }
//   if (!secret || secret.length < 12) {
//     return toast('Secret must be at least 12 characters', 'error');
//   }

//   const btn = document.getElementById('btn-register');
//   btn.disabled = true;
//   btn.innerHTML = '<span class="btn-icon btn-spinning">⟳</span> Registering…';

//   const { ok, data, error } = await registerAgent(name, secret, tools);

//   btn.disabled = false;
//   btn.innerHTML = '🚀 Register Agent';

//   if (!ok) {
//     toast(error || 'Registration failed', 'error');
//     return;
//   }

//   toast(`Agent "${name}" registered successfully!`, 'success');
//   setToken(data.access_token, data.agent_id, name);
//   document.getElementById('token-display').value = data.access_token;
// }

// async function handlePasteToken() {
//   const raw = document.getElementById('token-display').value.trim();
//   if (!raw) return toast('Paste a JWT token first', 'error');

//   try {
//     const parts = raw.split('.');
//     if (parts.length !== 3) throw new Error('Not a JWT');
//     const payload = JSON.parse(atob(parts[1].replace(/-/g,'+').replace(/_/g,'/')));
//     if (!payload.sub) throw new Error('Token has no sub claim');
//     setToken(raw, payload.sub, payload.agent_name || payload.sub.slice(0, 8) + '…');
//     toast('Token applied successfully', 'success');
//   } catch (e) {
//     toast(`Invalid token: ${e.message}`, 'error');
//   }
// }

// /* ═══════════════════════════════════════════════════════════════
//    5. OVERVIEW — stats cards + latency bars
// ═══════════════════════════════════════════════════════════════ */

// async function refreshOverview() {
//   if (state.loading.stats) return;
//   state.loading.stats = true;

//   renderStatSkeletons();

//   const { ok, data, error } = await fetchStats();
//   state.loading.stats = false;

//   if (!ok) {
//     toast(error || 'Failed to load stats', 'error');
//     renderStatsError();
//     return;
//   }

//   state.data.stats = data;
//   renderStats(data);
//   renderLatency(data.tool_latency || []);

//   const vRes = await fetchViolations(0, 5);
//   if (vRes.ok) renderOverviewViolations(vRes.data);
// }

// function renderStatSkeletons() {
//   const ids = ['stat-agents','stat-runs','stat-events','stat-violations','stat-rate','stat-interactions'];
//   ids.forEach(id => {
//     const el = document.getElementById(id);
//     if (el) el.innerHTML = '<span class="skeleton" style="display:inline-block;width:48px;height:28px;"></span>';
//   });
// }

// function renderStatsError() {
//   const ids = ['stat-agents','stat-runs','stat-events','stat-violations','stat-rate','stat-interactions'];
//   ids.forEach(id => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = '—';
//   });
// }

// function renderStats(s) {
//   const set = (id, val) => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = val;
//   };

//   set('stat-agents',       s.total_agents);
//   set('stat-runs',         s.total_runs);
//   set('stat-events',       s.total_events);
//   set('stat-violations',   s.total_violations);
//   set('stat-rate',         `${s.violation_rate.toFixed(1)}%`);
//   set('stat-interactions', s.total_interactions ?? 0);

//   const setSub = (id, val) => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = val;
//   };
//   setSub('stat-runs-sub', `${s.completed_runs} completed · ${s.failed_runs} failed`);
//   setSub('stat-violations-sub', s.total_tool_calls > 0
//     ? `out of ${s.total_tool_calls} tool calls`
//     : 'no tool calls yet');
// }

// function renderLatency(tools) {
//   const grid = document.getElementById('latency-grid');
//   if (!grid) return;

//   if (!tools || tools.length === 0) {
//     grid.innerHTML = `
//       <div class="empty-state" style="padding:24px; grid-column:1/-1;">
//         <span class="empty-icon">⏱</span>
//         <span class="empty-title">No tool executions yet</span>
//         <span class="empty-body">Run an agent with a prompt to see latency data here.</span>
//       </div>`;
//     return;
//   }

//   const maxMs = Math.max(...tools.map(t => t.p95_ms || t.avg_ms || 1), 1);

//   grid.innerHTML = tools.map(t => {
//     const avgPct = Math.min(100, ((t.avg_ms || 0) / maxMs) * 100).toFixed(1);
//     const p95Pct = Math.min(100, ((t.p95_ms || 0) / maxMs) * 100).toFixed(1);

//     return `
//       <div class="latency-card">
//         <div class="latency-header">
//           <span class="latency-tool-name">${escHtml(t.tool_name)}</span>
//           <span class="latency-calls">${t.call_count} calls</span>
//         </div>
//         <div class="latency-bar-row">
//           <span class="latency-bar-label">avg</span>
//           <div class="latency-bar-track">
//             <div class="latency-bar-fill" style="width:${avgPct}%"></div>
//           </div>
//           <span class="latency-val">${t.avg_ms != null ? t.avg_ms.toFixed(1) + 'ms' : '—'}</span>
//         </div>
//         <div class="latency-bar-row">
//           <span class="latency-bar-label">p95</span>
//           <div class="latency-bar-track">
//             <div class="latency-bar-fill p95" style="width:${p95Pct}%"></div>
//           </div>
//           <span class="latency-val">${t.p95_ms != null ? t.p95_ms.toFixed(1) + 'ms' : '—'}</span>
//         </div>
//       </div>`;
//   }).join('');
// }

// /* ═══════════════════════════════════════════════════════════════
//    5b. OVERVIEW VIOLATIONS PREVIEW
// ═══════════════════════════════════════════════════════════════ */

// function renderOverviewViolations(data) {
//   const tbody   = document.getElementById('overview-violations-tbody');
//   const counter = document.getElementById('overview-violations-count');
//   if (!tbody) return;

//   if (counter) counter.textContent = data.total;

//   if (!data.violations || data.violations.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="5">
//         <div class="empty-state" style="padding:20px;">
//           <span class="empty-icon">✅</span>
//           <span class="empty-title">No violations yet</span>
//         </div>
//       </td></tr>`;
//     return;
//   }

//   tbody.innerHTML = data.violations.slice(0, 5).map(v => {
//     const inputStr = v.attempted_input
//       ? JSON.stringify(v.attempted_input).slice(0, 50)
//       : '—';
//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(v.timestamp)}</td>
//         <td>${escHtml(v.agent_name || '—')}</td>
//         <td><span class="badge badge-tool">${escHtml(v.tool_name || '—')}</span></td>
//         <td>
//           <span class="violation-input"
//                 style="font-family:var(--font-mono);font-size:11.5px;">
//             ${escHtml(inputStr)}
//           </span>
//         </td>
//         <td style="font-size:12px;color:var(--text-secondary);
//                    max-width:180px;overflow:hidden;
//                    text-overflow:ellipsis;white-space:nowrap;">
//           ${escHtml((v.denial_message || '').slice(0, 60))}
//         </td>
//       </tr>`;
//   }).join('');
// }

// /* ═══════════════════════════════════════════════════════════════
//    6. VIOLATIONS TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshViolations() {
//   if (state.loading.violations) return;
//   state.loading.violations = true;
//   setTableLoading('violations-tbody');

//   const skip  = state.filters.violationPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchViolations(skip, state.filters.pageSize);
//   state.loading.violations = false;

//   if (!ok) {
//     toast(error || 'Failed to load violations', 'error');
//     return;
//   }

//   state.data.violations = data;
//   renderViolations(data);
// }

// function renderViolations(data) {
//   const tbody   = document.getElementById('violations-tbody');
//   const counter = document.getElementById('violations-count');
//   if (counter) counter.textContent = data.total;

//   if (!data.violations || data.violations.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="6">
//         <div class="empty-state">
//           <span class="empty-icon">✅</span>
//           <span class="empty-title">No governance violations</span>
//           <span class="empty-body">All tool calls are within permitted bounds.</span>
//         </div>
//       </td></tr>`;
//     renderPagination('violations-pagination', data, state.filters.violationPage, (p) => {
//       state.filters.violationPage = p;
//       refreshViolations();
//     });
//     return;
//   }

//   tbody.innerHTML = data.violations.map(v => {
//     const inputStr = v.attempted_input ? JSON.stringify(v.attempted_input) : '—';
//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(v.timestamp)}</td>
//         <td><span class="badge badge-violation">⛔ violation</span></td>
//         <td>
//           <span style="font-weight:500;">${escHtml(v.agent_name || '—')}</span>
//           <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(v.agent_id.slice(0,8))}…</span>
//         </td>
//         <td><span class="badge badge-tool">${escHtml(v.tool_name || '—')}</span></td>
//         <td>
//           <div class="violation-detail">
//             <span class="violation-input">${escHtml(inputStr)}</span>
//           </div>
//         </td>
//         <td>
//           <div style="max-width:220px; font-size:12px; color:var(--text-secondary); line-height:1.5;">
//             ${escHtml(v.denial_message || '—')}
//           </div>
//         </td>
//       </tr>`;
//   }).join('');

//   renderPagination('violations-pagination', data, state.filters.violationPage, (p) => {
//     state.filters.violationPage = p;
//     refreshViolations();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    7. AUDIT LOG TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshAuditLog() {
//   if (state.loading.audit) return;
//   state.loading.audit = true;
//   setTableLoading('audit-tbody');

//   const skip = state.filters.auditPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchAuditLogs(
//     skip,
//     state.filters.pageSize,
//     state.filters.auditEventType,
//   );
//   state.loading.audit = false;

//   if (!ok) {
//     toast(error || 'Failed to load audit log', 'error');
//     return;
//   }

//   state.data.audit = data;
//   renderAuditLog(data);
// }

// function renderAuditLog(data) {
//   const tbody   = document.getElementById('audit-tbody');
//   const counter = document.getElementById('audit-count');
//   if (counter) counter.textContent = data.total;

//   if (!data.events || data.events.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="7">
//         <div class="empty-state">
//           <span class="empty-icon">📋</span>
//           <span class="empty-title">No audit events</span>
//           <span class="empty-body">Submit a run via POST /agents/run to populate the audit log.</span>
//         </div>
//       </td></tr>`;
//     renderPagination('audit-pagination', data, state.filters.auditPage, (p) => {
//       state.filters.auditPage = p;
//       refreshAuditLog();
//     });
//     return;
//   }

//   tbody.innerHTML = data.events.map(e => {
//     const inputSummary = e.input_data
//       ? JSON.stringify(e.input_data).slice(0, 60) + (JSON.stringify(e.input_data).length > 60 ? '…' : '')
//       : '—';

//     const permittedBadge = e.permitted === true
//       ? '<span class="badge badge-permitted">✓ allowed</span>'
//       : e.permitted === false
//         ? '<span class="badge badge-blocked">⛔ blocked</span>'
//         : '<span style="color:var(--text-muted);font-size:12px;">—</span>';

//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(e.timestamp)}</td>
//         <td>${eventTypeBadge(e.event_type)}</td>
//         <td><span style="font-weight:500;">${escHtml(e.agent_name || '—')}</span></td>
//         <td>
//           ${e.tool_name
//             ? `<span class="badge badge-tool">${escHtml(e.tool_name)}</span>`
//             : '<span class="td-dim">—</span>'
//           }
//         </td>
//         <td>${permittedBadge}</td>
//         <td>
//           <div class="td-truncate td-mono" style="font-size:11.5px; color:var(--text-secondary);">
//             ${escHtml(inputSummary)}
//           </div>
//         </td>
//         <td>
//           ${e.latency_ms != null
//             ? `<span class="td-mono" style="font-size:12px; color:var(--accent-teal);">${e.latency_ms.toFixed(1)}ms</span>`
//             : '<span class="td-dim">—</span>'
//           }
//         </td>
//       </tr>`;
//   }).join('');

//   renderPagination('audit-pagination', data, state.filters.auditPage, (p) => {
//     state.filters.auditPage = p;
//     refreshAuditLog();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    7b. AGENT INTERACTIONS TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshInteractions() {
//   if (state.loading.interactions) return;
//   state.loading.interactions = true;
//   setTableLoading('interactions-tbody');

//   const skip  = state.filters.interactionsPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchInteractions(skip, state.filters.pageSize);
//   state.loading.interactions = false;

//   if (!ok) {
//     toast(error || 'Failed to load interactions', 'error');
//     return;
//   }

//   state.data.interactions = data;
//   renderInteractions(data);
// }

// function renderInteractions(data) {
//   const tbody   = document.getElementById('interactions-tbody');
//   const counter = document.getElementById('interactions-count');
//   if (counter) counter.textContent = data.total;

//   if (!data.interactions || data.interactions.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="5">
//         <div class="empty-state">
//           <span class="empty-icon">🔗</span>
//           <span class="empty-title">No agent interactions yet</span>
//           <span class="empty-body">
//             Use POST /agent-interactions to record a handoff, delegation,
//             request, or response between two registered agents.
//           </span>
//         </div>
//       </td></tr>`;
//     renderPagination('interactions-pagination', data, state.filters.interactionsPage, (p) => {
//       state.filters.interactionsPage = p;
//       refreshInteractions();
//     });
//     return;
//   }

//   const typeBadge = (type) => {
//     const map = {
//       'handoff':    '<span class="badge badge-handoff">⇒ handoff</span>',
//       'delegation': '<span class="badge badge-delegation">↓ delegation</span>',
//       'request':    '<span class="badge badge-request">? request</span>',
//       'response':   '<span class="badge badge-response">✓ response</span>',
//     };
//     return map[type] || `<span class="badge">${escHtml(type)}</span>`;
//   };

//   tbody.innerHTML = data.interactions.map(i => `
//     <tr>
//       <td class="td-mono td-dim">${relativeTime(i.created_at)}</td>
//       <td>${typeBadge(i.interaction_type)}</td>
//       <td>
//         <span style="font-weight:500;">${escHtml(i.source_agent_name || '—')}</span>
//         <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(i.source_agent_id.slice(0,8))}…</span>
//       </td>
//       <td>
//         <span style="font-weight:500;">${escHtml(i.target_agent_name || '—')}</span>
//         <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(i.target_agent_id.slice(0,8))}…</span>
//       </td>
//       <td>
//         <div style="max-width:260px; font-size:12px; color:var(--text-secondary);
//                     overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
//           ${i.message ? escHtml(i.message) : '<span class="td-dim">—</span>'}
//         </div>
//       </td>
//     </tr>`).join('');

//   renderPagination('interactions-pagination', data, state.filters.interactionsPage, (p) => {
//     state.filters.interactionsPage = p;
//     refreshInteractions();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    8. UTILITIES
// ═══════════════════════════════════════════════════════════════ */

// function escHtml(str) {
//   if (str == null) return '';
//   return String(str)
//     .replace(/&/g, '&amp;')
//     .replace(/</g, '&lt;')
//     .replace(/>/g, '&gt;')
//     .replace(/"/g, '&quot;');
// }

// function relativeTime(isoString) {
//   if (!isoString) return '—';
//   const date = new Date(isoString.endsWith('Z') ? isoString : isoString + 'Z');
//   const diff = (Date.now() - date.getTime()) / 1000;
//   if (diff < 5)    return 'just now';
//   if (diff < 60)   return `${Math.floor(diff)}s ago`;
//   if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
//   if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
//   return date.toLocaleDateString();
// }

// function eventTypeBadge(type) {
//   const map = {
//     'run_start':    '<span class="badge badge-run-start">▶ run_start</span>',
//     'tool_call':    '<span class="badge badge-tool-call">⚙ tool_call</span>',
//     'tool_end':     '<span class="badge badge-tool-end">✓ tool_end</span>',
//     'violation':    '<span class="badge badge-violation">⛔ violation</span>',
//     'run_end':      '<span class="badge badge-run-end">■ run_end</span>',
//     'agent_handoff':'<span class="badge badge-handoff">⇒ agent_handoff</span>',
//   };
//   return map[type] || `<span class="badge">${escHtml(type)}</span>`;
// }

// function setTableLoading(tbodyId) {
//   const el = document.getElementById(tbodyId);
//   if (!el) return;
//   el.innerHTML = Array.from({ length: 5 }, () => `
//     <tr>${Array.from({ length: 7 }, () =>
//       `<td><div class="skeleton" style="height:16px; width:${60 + Math.random()*60 | 0}px;"></div></td>`
//     ).join('')}</tr>
//   `).join('');
// }

// function renderPagination(containerId, data, currentPage, onPage) {
//   const el = document.getElementById(containerId);
//   if (!el) return;

//   const totalPages = Math.ceil(data.total / (data.limit || 25));
//   if (totalPages <= 1) { el.innerHTML = ''; return; }

//   const prevDisabled = currentPage === 0;
//   const nextDisabled = currentPage >= totalPages - 1;

//   el.innerHTML = `
//     <div style="display:flex; align-items:center; gap:10px; justify-content:flex-end; padding:12px 14px; border-top:1px solid var(--border);">
//       <span style="font-size:12px; color:var(--text-muted);">
//         ${data.skip + 1}–${Math.min(data.skip + data.limit, data.total)} of ${data.total}
//       </span>
//       <button class="btn btn-ghost btn-sm" ${prevDisabled ? 'disabled' : ''} onclick="(${onPage.toString()})(${currentPage - 1})">
//         ← Prev
//       </button>
//       <button class="btn btn-ghost btn-sm" ${nextDisabled ? 'disabled' : ''} onclick="(${onPage.toString()})(${currentPage + 1})">
//         Next →
//       </button>
//     </div>`;
// }

// function updateConnectionStatus(status, label) {
//   const dot  = document.getElementById('status-dot');
//   const text = document.getElementById('status-text');
//   if (dot)  dot.className = `status-dot ${status}`;
//   if (text) text.textContent = label || status;
// }

// function updateLastRefresh() {
//   state.lastRefresh = new Date();
//   const el = document.getElementById('last-refresh');
//   if (el) el.textContent = `Refreshed ${relativeTime(state.lastRefresh.toISOString())}`;
// }

// let _refreshLabelTimer = null;
// function startRefreshLabelUpdater() {
//   clearInterval(_refreshLabelTimer);
//   _refreshLabelTimer = setInterval(() => {
//     if (!state.lastRefresh) return;
//     const el = document.getElementById('last-refresh');
//     if (el) el.textContent = `Refreshed ${relativeTime(state.lastRefresh.toISOString())}`;
//   }, 15_000);
// }

// function toast(message, type = 'info') {
//   const container = document.getElementById('toast-container');
//   if (!container) return;
//   const icons = { error: '⚠', success: '✓', info: 'ℹ' };
//   const el = document.createElement('div');
//   el.className = `toast ${type}`;
//   el.innerHTML = `<span>${icons[type] || icons.info}</span><span>${escHtml(message)}</span>`;
//   container.appendChild(el);
//   setTimeout(() => {
//     el.style.animation = 'toast-out 0.2s ease forwards';
//     setTimeout(() => el.remove(), 200);
//   }, 3500);
// }

// /* ═══════════════════════════════════════════════════════════════
//    9. AUTO-REFRESH + INIT
// ═══════════════════════════════════════════════════════════════ */

// const REFRESH_INTERVAL_MS = 30_000;

// function refreshCurrentView() {
//   if (!state.token) return;

//   const btn = document.getElementById('btn-refresh');
//   if (btn) {
//     btn.classList.add('btn-spinning');
//     btn.querySelector('.btn-icon').style.animation = 'spin 0.8s linear infinite';
//   }

//   const views = {
//     overview:     refreshOverview,
//     violations:   refreshViolations,
//     audit:        refreshAuditLog,
//     interactions: refreshInteractions,
//   };
//   const fn = views[state.currentView];
//   if (fn) fn().finally(() => {
//     updateLastRefresh();
//     if (btn) btn.querySelector('.btn-icon').style.animation = '';
//   });
// }

// function startAutoRefresh() {
//   stopAutoRefresh();
//   state.refreshTimer = setInterval(refreshCurrentView, REFRESH_INTERVAL_MS);
// }

// function stopAutoRefresh() {
//   if (state.refreshTimer) clearInterval(state.refreshTimer);
//   state.refreshTimer = null;
// }

// document.addEventListener('DOMContentLoaded', () => {

//   document.querySelectorAll('.nav-item[data-view]').forEach(el => {
//     el.addEventListener('click', () => navigateTo(el.dataset.view));
//   });

//   document.getElementById('btn-refresh')?.addEventListener('click', refreshCurrentView);

//   document.getElementById('btn-disconnect')?.addEventListener('click', () => {
//     clearToken();
//     toast('Disconnected', 'info');
//   });

//   document.getElementById('btn-register')?.addEventListener('click', handleRegister);

//   document.getElementById('btn-use-token')?.addEventListener('click', handlePasteToken);

//   document.getElementById('audit-filter-type')?.addEventListener('change', (e) => {
//     state.filters.auditEventType = e.target.value;
//     state.filters.auditPage      = 0;
//     refreshAuditLog();
//   });

//   document.getElementById('mobile-menu-btn')?.addEventListener('click', () => {
//     document.querySelector('.sidebar')?.classList.toggle('open');
//   });

//   startRefreshLabelUpdater();

//   if (!tryRestoreSession()) {
//     showAuthBanner();
//     updateConnectionStatus('disconnected', 'Not connected');
//   }
// });

























// /**
//  * app.js — AgentWatch Dashboard
//  *
//  * Architecture:
//  *   State → API → Render pipeline.
//  *   No framework, no bundler.  Vanilla ES2022 modules pattern
//  *   (everything in one file for simplicity — split if the project grows).
//  *
//  * Sections:
//  *   1. State management
//  *   2. API client (all fetch calls, token injection)
//  *   3. Navigation / view router
//  *   4. Auth flow (register / paste token)
//  *   5. Render: Overview (stats + latency)
//  *   6. Render: Violations table
//  *   7. Render: Audit log table
//  *   8. Utilities (badges, timestamps, toast)
//  *   9. Auto-refresh + init
//  */

// 'use strict';

// /* ═══════════════════════════════════════════════════════════════
//    1. STATE
// ═══════════════════════════════════════════════════════════════ */

// const state = {
//   token:           null,       // JWT bearer token
//   agentId:         null,       // UUID of the authenticated agent
//   agentName:       null,
//   lastRefresh:     null,
//   refreshTimer:    null,
//   currentView:     'overview',
//   loading: {
//     stats:        false,
//     violations:   false,
//     audit:        false,
//     interactions: false,
//     policies:     false,
//   },
//   data: {
//     stats:        null,
//     violations:   null,    // { violations: [], total, skip, limit }
//     audit:        null,    // { events: [], total, skip, limit }
//     interactions: null,    // { interactions: [], total, skip, limit }
//     policies:     null,    // { policies: [], total, skip, limit }
//   },
//   filters: {
//     auditEventType:    '',
//     violationPage:     0,
//     auditPage:         0,
//     interactionsPage:  0,
//     policiesPage:      0,
//     pageSize:          25,
//   },
// };

// /* ═══════════════════════════════════════════════════════════════
//    2. API CLIENT
// ═══════════════════════════════════════════════════════════════ */

// const API_BASE = '';  // Same origin — served by FastAPI at /dashboard/

// function authHeaders() {
//   return {
//     'Content-Type': 'application/json',
//     ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
//   };
// }

// /**
//  * Generic fetch wrapper.
//  * Returns { ok: true, data } or { ok: false, error, status }.
//  */
// async function apiFetch(path, opts = {}) {
//   try {
//     const res = await fetch(`${API_BASE}${path}`, {
//       ...opts,
//       headers: { ...authHeaders(), ...(opts.headers || {}) },
//     });
//     const data = await res.json().catch(() => null);
//     if (!res.ok) {
//       return { ok: false, error: data?.detail || `HTTP ${res.status}`, status: res.status };
//     }
//     return { ok: true, data };
//   } catch (err) {
//     return { ok: false, error: err.message || 'Network error', status: 0 };
//   }
// }

// async function fetchStats() {
//   return apiFetch('/analytics/stats');
// }

// async function fetchViolations(skip = 0, limit = 25) {
//   return apiFetch(`/governance/violations?skip=${skip}&limit=${limit}`);
// }

// async function fetchInteractions(skip = 0, limit = 25) {
//   return apiFetch(`/agent-interactions?skip=${skip}&limit=${limit}`);
// }

// async function fetchAuditLogs(skip = 0, limit = 25, eventType = '') {
//   const et = eventType ? `&event_type=${encodeURIComponent(eventType)}` : '';
//   return apiFetch(`/audit/logs?skip=${skip}&limit=${limit}${et}`);
// }

// async function fetchPolicies(skip = 0, limit = 25) {
//   return apiFetch(`/policies?skip=${skip}&limit=${limit}`);
// }

// async function registerAgent(name, secret, tools) {
//   return apiFetch('/agents/register', {
//     method: 'POST',
//     body: JSON.stringify({ name, secret, allowed_tools: tools }),
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    3. NAVIGATION
// ═══════════════════════════════════════════════════════════════ */

// function navigateTo(viewId) {
//   state.currentView = viewId;

//   // Update sidebar active state
//   document.querySelectorAll('.nav-item').forEach(el => {
//     el.classList.toggle('active', el.dataset.view === viewId);
//   });

//   // Swap visible view
//   document.querySelectorAll('.view').forEach(el => {
//     el.classList.toggle('active', el.id === `view-${viewId}`);
//   });

//   // Update topbar title
//   const titles = {
//     overview:     { title: 'Overview',              sub: 'Platform health at a glance' },
//     violations:   { title: 'Governance Violations', sub: 'Blocked tool calls and policy enforcement' },
//     audit:        { title: 'Audit Log',             sub: 'Full event trace across all agent runs' },
//     interactions: { title: 'Agent Interactions',    sub: 'Agent-to-agent communication records' },
//     policies:     { title: 'Governance Policies',   sub: 'Named rules enforced before every run' },
//   };
//   const t = titles[viewId] || {};
//   document.getElementById('topbar-title').textContent    = t.title || '';
//   document.getElementById('topbar-subtitle').textContent = t.sub   || '';

//   // Load data for the view
//   if (!state.token) return;
//   if (viewId === 'overview')     refreshOverview();
//   if (viewId === 'violations')   refreshViolations();
//   if (viewId === 'audit')        refreshAuditLog();
//   if (viewId === 'interactions') refreshInteractions();
//   if (viewId === 'policies')     refreshPolicies();
// }

// /* ═══════════════════════════════════════════════════════════════
//    4. AUTH FLOW
// ═══════════════════════════════════════════════════════════════ */

// function setToken(token, agentId, agentName) {
//   state.token     = token;
//   state.agentId   = agentId;
//   state.agentName = agentName || 'unknown';

//   // Persist in sessionStorage (not localStorage — clears on tab close)
//   sessionStorage.setItem('aw_token',      token);
//   sessionStorage.setItem('aw_agent_id',   agentId);
//   sessionStorage.setItem('aw_agent_name', agentName || '');

//   updateConnectionStatus('connected', agentName || agentId?.slice(0, 8) + '…');
//   hideAuthBanner();
//   startAutoRefresh();
//   navigateTo(state.currentView);
// }

// function clearToken() {
//   state.token = state.agentId = state.agentName = null;
//   sessionStorage.removeItem('aw_token');
//   sessionStorage.removeItem('aw_agent_id');
//   sessionStorage.removeItem('aw_agent_name');
//   stopAutoRefresh();
//   updateConnectionStatus('disconnected', 'Not connected');
//   showAuthBanner();
// }

// function tryRestoreSession() {
//   const token     = sessionStorage.getItem('aw_token');
//   const agentId   = sessionStorage.getItem('aw_agent_id');
//   const agentName = sessionStorage.getItem('aw_agent_name');
//   if (token && agentId) {
//     setToken(token, agentId, agentName);
//     return true;
//   }
//   return false;
// }

// function showAuthBanner() {
//   document.getElementById('auth-banner').style.display = 'block';
// }

// function hideAuthBanner() {
//   document.getElementById('auth-banner').style.display = 'none';
// }

// async function handleRegister() {
//   const name   = document.getElementById('reg-name').value.trim();
//   const secret = document.getElementById('reg-secret').value;
//   const tools  = Array.from(
//     document.querySelectorAll('.tool-checkbox:checked')
//   ).map(el => el.value);

//   if (!name || name.length < 2) {
//     return toast('Agent name must be at least 2 characters', 'error');
//   }
//   if (!secret || secret.length < 12) {
//     return toast('Secret must be at least 12 characters', 'error');
//   }

//   const btn = document.getElementById('btn-register');
//   btn.disabled = true;
//   btn.innerHTML = '<span class="btn-icon btn-spinning">⟳</span> Registering…';

//   const { ok, data, error } = await registerAgent(name, secret, tools);

//   btn.disabled = false;
//   btn.innerHTML = '🚀 Register Agent';

//   if (!ok) {
//     toast(error || 'Registration failed', 'error');
//     return;
//   }

//   toast(`Agent "${name}" registered successfully!`, 'success');
//   setToken(data.access_token, data.agent_id, name);
//   document.getElementById('token-display').value = data.access_token;
// }

// async function handlePasteToken() {
//   const raw = document.getElementById('token-display').value.trim();
//   if (!raw) return toast('Paste a JWT token first', 'error');

//   // Decode payload to extract sub (agent_id) and agent_name — no library needed
//   try {
//     const parts = raw.split('.');
//     if (parts.length !== 3) throw new Error('Not a JWT');
//     const payload = JSON.parse(atob(parts[1].replace(/-/g,'+').replace(/_/g,'/')));
//     if (!payload.sub) throw new Error('Token has no sub claim');
//     setToken(raw, payload.sub, payload.agent_name || payload.sub.slice(0, 8) + '…');
//     toast('Token applied successfully', 'success');
//   } catch (e) {
//     toast(`Invalid token: ${e.message}`, 'error');
//   }
// }

// /* ═══════════════════════════════════════════════════════════════
//    5. OVERVIEW — stats cards + latency bars
// ═══════════════════════════════════════════════════════════════ */

// async function refreshOverview() {
//   if (state.loading.stats) return;
//   state.loading.stats = true;

//   // Show skeleton values while loading
//   renderStatSkeletons();

//   const { ok, data, error } = await fetchStats();
//   state.loading.stats = false;

//   if (!ok) {
//     toast(error || 'Failed to load stats', 'error');
//     renderStatsError();
//     return;
//   }

//   state.data.stats = data;
//   renderStats(data);
//   renderLatency(data.tool_latency || []);

//   // Fetch the 5 most recent violations for the overview preview panel.
//   // Separate lightweight call (limit=5) made only when overview is active.
//   // renderViolations on the Violations page is never involved.
//   const vRes = await fetchViolations(0, 5);
//   if (vRes.ok) renderOverviewViolations(vRes.data);
// }

// function renderStatSkeletons() {
//   const ids = ['stat-agents','stat-runs','stat-events','stat-violations','stat-rate','stat-completed'];
//   ids.forEach(id => {
//     const el = document.getElementById(id);
//     if (el) el.innerHTML = '<span class="skeleton" style="display:inline-block;width:48px;height:28px;"></span>';
//   });
// }

// function renderStatsError() {
//   const ids = ['stat-agents','stat-runs','stat-events','stat-violations','stat-rate','stat-completed'];
//   ids.forEach(id => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = '—';
//   });
// }

// function renderStats(s) {
//   const set = (id, val) => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = val;
//   };

//   set('stat-agents',    s.total_agents);
//   set('stat-runs',      s.total_runs);
//   set('stat-events',    s.total_events);
//   set('stat-violations', s.total_violations);
//   set('stat-rate',      `${s.violation_rate.toFixed(1)}%`);
//   set('stat-completed', s.completed_runs);

//   // Sub-lines
//   const setSub = (id, val) => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = val;
//   };
//   setSub('stat-runs-sub',       `${s.completed_runs} completed · ${s.failed_runs} failed`);
//   setSub('stat-violations-sub', s.total_tool_calls > 0
//     ? `out of ${s.total_tool_calls} tool calls`
//     : 'no tool calls yet');
// }

// function renderLatency(tools) {
//   const grid = document.getElementById('latency-grid');
//   if (!grid) return;

//   if (!tools || tools.length === 0) {
//     grid.innerHTML = `
//       <div class="empty-state" style="padding:24px; grid-column:1/-1;">
//         <span class="empty-icon">⏱</span>
//         <span class="empty-title">No tool executions yet</span>
//         <span class="empty-body">Run an agent with a prompt to see latency data here.</span>
//       </div>`;
//     return;
//   }

//   const maxMs = Math.max(...tools.map(t => t.p95_ms || t.avg_ms || 1), 1);

//   grid.innerHTML = tools.map(t => {
//     const avgPct = Math.min(100, ((t.avg_ms || 0) / maxMs) * 100).toFixed(1);
//     const p95Pct = Math.min(100, ((t.p95_ms || 0) / maxMs) * 100).toFixed(1);

//     return `
//       <div class="latency-card">
//         <div class="latency-header">
//           <span class="latency-tool-name">${escHtml(t.tool_name)}</span>
//           <span class="latency-calls">${t.call_count} calls</span>
//         </div>
//         <div class="latency-bar-row">
//           <span class="latency-bar-label">avg</span>
//           <div class="latency-bar-track">
//             <div class="latency-bar-fill" style="width:${avgPct}%"></div>
//           </div>
//           <span class="latency-val">${t.avg_ms != null ? t.avg_ms.toFixed(1) + 'ms' : '—'}</span>
//         </div>
//         <div class="latency-bar-row">
//           <span class="latency-bar-label">p95</span>
//           <div class="latency-bar-track">
//             <div class="latency-bar-fill p95" style="width:${p95Pct}%"></div>
//           </div>
//           <span class="latency-val">${t.p95_ms != null ? t.p95_ms.toFixed(1) + 'ms' : '—'}</span>
//         </div>
//       </div>`;
//   }).join('');
// }

// /* ═══════════════════════════════════════════════════════════════
//    5b. OVERVIEW VIOLATIONS PREVIEW
//    Renders the 5-row preview panel on the Overview page.
//    Uses data already fetched by refreshOverview() — no extra API call.
//    Completely independent of renderViolations(); neither calls the other.
// ═══════════════════════════════════════════════════════════════ */

// function renderOverviewViolations(data) {
//   const tbody   = document.getElementById('overview-violations-tbody');
//   const counter = document.getElementById('overview-violations-count');
//   if (!tbody) return;

//   if (counter) counter.textContent = data.total;

//   if (!data.violations || data.violations.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="5">
//         <div class="empty-state" style="padding:20px;">
//           <span class="empty-icon">✅</span>
//           <span class="empty-title">No violations yet</span>
//         </div>
//       </td></tr>`;
//     return;
//   }

//   tbody.innerHTML = data.violations.slice(0, 5).map(v => {
//     const inputStr = v.attempted_input
//       ? JSON.stringify(v.attempted_input).slice(0, 50)
//       : '—';
//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(v.timestamp)}</td>
//         <td>${escHtml(v.agent_name || '—')}</td>
//         <td><span class="badge badge-tool">${escHtml(v.tool_name || '—')}</span></td>
//         <td>
//           <span class="violation-input"
//                 style="font-family:var(--font-mono);font-size:11.5px;">
//             ${escHtml(inputStr)}
//           </span>
//         </td>
//         <td style="font-size:12px;color:var(--text-secondary);
//                    max-width:180px;overflow:hidden;
//                    text-overflow:ellipsis;white-space:nowrap;">
//           ${escHtml((v.denial_message || '').slice(0, 60))}
//         </td>
//       </tr>`;
//   }).join('');
// }

// /* ═══════════════════════════════════════════════════════════════
//    6. VIOLATIONS TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshViolations() {
//   if (state.loading.violations) return;
//   state.loading.violations = true;
//   setTableLoading('violations-tbody');

//   const skip  = state.filters.violationPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchViolations(skip, state.filters.pageSize);
//   state.loading.violations = false;

//   if (!ok) {
//     toast(error || 'Failed to load violations', 'error');
//     return;
//   }

//   state.data.violations = data;
//   renderViolations(data);
// }

// function renderViolations(data) {
//   const tbody   = document.getElementById('violations-tbody');
//   const counter = document.getElementById('violations-count');
//   if (counter) counter.textContent = data.total;

//   if (!data.violations || data.violations.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="6">
//         <div class="empty-state">
//           <span class="empty-icon">✅</span>
//           <span class="empty-title">No governance violations</span>
//           <span class="empty-body">All tool calls are within permitted bounds. Register an agent with limited tools and run a prompt to see violations here.</span>
//         </div>
//       </td></tr>`;
//     renderPagination('violations-pagination', data, state.filters.violationPage, (p) => {
//       state.filters.violationPage = p;
//       refreshViolations();
//     });
//     return;
//   }

//   tbody.innerHTML = data.violations.map(v => {
//     const inputStr = v.attempted_input
//       ? JSON.stringify(v.attempted_input)
//       : '—';

//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(v.timestamp)}</td>
//         <td>
//           <span class="badge badge-violation">⛔ violation</span>
//         </td>
//         <td>
//           <span style="font-weight:500;">${escHtml(v.agent_name || '—')}</span>
//           <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(v.agent_id.slice(0,8))}…</span>
//         </td>
//         <td><span class="badge badge-tool">${escHtml(v.tool_name || '—')}</span></td>
//         <td>
//           <div class="violation-detail">
//             <span class="violation-input">${escHtml(inputStr)}</span>
//           </div>
//         </td>
//         <td>
//           <div style="max-width:220px; font-size:12px; color:var(--text-secondary); line-height:1.5;">
//             ${escHtml(v.denial_message || '—')}
//           </div>
//         </td>
//       </tr>`;
//   }).join('');

//   renderPagination('violations-pagination', data, state.filters.violationPage, (p) => {
//     state.filters.violationPage = p;
//     refreshViolations();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    7. AUDIT LOG TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshAuditLog() {
//   if (state.loading.audit) return;
//   state.loading.audit = true;
//   setTableLoading('audit-tbody');

//   const skip = state.filters.auditPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchAuditLogs(
//     skip,
//     state.filters.pageSize,
//     state.filters.auditEventType,
//   );
//   state.loading.audit = false;

//   if (!ok) {
//     toast(error || 'Failed to load audit log', 'error');
//     return;
//   }

//   state.data.audit = data;
//   renderAuditLog(data);
// }

// function renderAuditLog(data) {
//   const tbody   = document.getElementById('audit-tbody');
//   const counter = document.getElementById('audit-count');
//   if (counter) counter.textContent = data.total;

//   if (!data.events || data.events.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="7">
//         <div class="empty-state">
//           <span class="empty-icon">📋</span>
//           <span class="empty-title">No audit events</span>
//           <span class="empty-body">Submit a run via POST /agents/run to populate the audit log.</span>
//         </div>
//       </td></tr>`;
//     renderPagination('audit-pagination', data, state.filters.auditPage, (p) => {
//       state.filters.auditPage = p;
//       refreshAuditLog();
//     });
//     return;
//   }

//   tbody.innerHTML = data.events.map(e => {
//     const inputSummary = e.input_data
//       ? JSON.stringify(e.input_data).slice(0, 60) + (JSON.stringify(e.input_data).length > 60 ? '…' : '')
//       : '—';

//     const outputSummary = e.output_data
//       ? JSON.stringify(e.output_data).slice(0, 60) + (JSON.stringify(e.output_data).length > 60 ? '…' : '')
//       : '—';

//     const permittedBadge = e.permitted === true
//       ? '<span class="badge badge-permitted">✓ allowed</span>'
//       : e.permitted === false
//         ? '<span class="badge badge-blocked">⛔ blocked</span>'
//         : '<span style="color:var(--text-muted);font-size:12px;">—</span>';

//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(e.timestamp)}</td>
//         <td>${eventTypeBadge(e.event_type)}</td>
//         <td>
//           <span style="font-weight:500;">${escHtml(e.agent_name || '—')}</span>
//         </td>
//         <td>
//           ${e.tool_name
//             ? `<span class="badge badge-tool">${escHtml(e.tool_name)}</span>`
//             : '<span class="td-dim">—</span>'
//           }
//         </td>
//         <td>${permittedBadge}</td>
//         <td>
//           <div class="td-truncate td-mono" style="font-size:11.5px; color:var(--text-secondary);">
//             ${escHtml(inputSummary)}
//           </div>
//         </td>
//         <td>
//           ${e.latency_ms != null
//             ? `<span class="td-mono" style="font-size:12px; color:var(--accent-teal);">${e.latency_ms.toFixed(1)}ms</span>`
//             : '<span class="td-dim">—</span>'
//           }
//         </td>
//       </tr>`;
//   }).join('');

//   renderPagination('audit-pagination', data, state.filters.auditPage, (p) => {
//     state.filters.auditPage = p;
//     refreshAuditLog();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    7b. AGENT INTERACTIONS TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshInteractions() {
//   if (state.loading.interactions) return;
//   state.loading.interactions = true;
//   setTableLoading('interactions-tbody');

//   const skip  = state.filters.interactionsPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchInteractions(skip, state.filters.pageSize);
//   state.loading.interactions = false;

//   if (!ok) {
//     toast(error || 'Failed to load interactions', 'error');
//     return;
//   }

//   state.data.interactions = data;
//   renderInteractions(data);
// }

// function renderInteractions(data) {
//   const tbody   = document.getElementById('interactions-tbody');
//   const counter = document.getElementById('interactions-count');
//   if (counter) counter.textContent = data.total;

//   if (!data.interactions || data.interactions.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="5">
//         <div class="empty-state">
//           <span class="empty-icon">🔗</span>
//           <span class="empty-title">No agent interactions yet</span>
//           <span class="empty-body">
//             Use POST /agent-interactions to record a handoff, delegation,
//             request, or response between two registered agents.
//           </span>
//         </div>
//       </td></tr>`;
//     renderPagination('interactions-pagination', data, state.filters.interactionsPage, (p) => {
//       state.filters.interactionsPage = p;
//       refreshInteractions();
//     });
//     return;
//   }

//   const typeBadge = (type) => {
//     const map = {
//       'handoff':    '<span class="badge badge-handoff">⇒ handoff</span>',
//       'delegation': '<span class="badge badge-delegation">↓ delegation</span>',
//       'request':    '<span class="badge badge-request">? request</span>',
//       'response':   '<span class="badge badge-response">✓ response</span>',
//     };
//     return map[type] || `<span class="badge">${escHtml(type)}</span>`;
//   };

//   tbody.innerHTML = data.interactions.map(i => `
//     <tr>
//       <td class="td-mono td-dim">${relativeTime(i.created_at)}</td>
//       <td>${typeBadge(i.interaction_type)}</td>
//       <td>
//         <span style="font-weight:500;">${escHtml(i.source_agent_name || '—')}</span>
//         <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(i.source_agent_id.slice(0,8))}…</span>
//       </td>
//       <td>
//         <span style="font-weight:500;">${escHtml(i.target_agent_name || '—')}</span>
//         <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(i.target_agent_id.slice(0,8))}…</span>
//       </td>
//       <td>
//         <div style="max-width:260px; font-size:12px; color:var(--text-secondary);
//                     overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
//           ${i.message ? escHtml(i.message) : '<span class="td-dim">—</span>'}
//         </div>
//       </td>
//     </tr>`).join('');

//   renderPagination('interactions-pagination', data, state.filters.interactionsPage, (p) => {
//     state.filters.interactionsPage = p;
//     refreshInteractions();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    8. UTILITIES
// ═══════════════════════════════════════════════════════════════ */

// function escHtml(str) {
//   if (str == null) return '';
//   return String(str)
//     .replace(/&/g, '&amp;')
//     .replace(/</g, '&lt;')
//     .replace(/>/g, '&gt;')
//     .replace(/"/g, '&quot;');
// }

// function relativeTime(isoString) {
//   if (!isoString) return '—';
//   const date  = new Date(isoString.endsWith('Z') ? isoString : isoString + 'Z');
//   const diff  = (Date.now() - date.getTime()) / 1000;
//   if (diff < 5)   return 'just now';
//   if (diff < 60)  return `${Math.floor(diff)}s ago`;
//   if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
//   if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
//   return date.toLocaleDateString();
// }

// function eventTypeBadge(type) {
//   const map = {
//     'run_start': '<span class="badge badge-run-start">▶ run_start</span>',
//     'tool_call': '<span class="badge badge-tool-call">⚙ tool_call</span>',
//     'tool_end':  '<span class="badge badge-tool-end">✓ tool_end</span>',
//     'violation': '<span class="badge badge-violation">⛔ violation</span>',
//     'run_end':   '<span class="badge badge-run-end">■ run_end</span>',
//     'agent_handoff':    '<span class="badge badge-handoff">⇒ agent_handoff</span>',
//     'policy_violation': '<span class="badge badge-violation">🛡 policy_violation</span>',
//   };
//   return map[type] || `<span class="badge">${escHtml(type)}</span>`;
// }

// function setTableLoading(tbodyId) {
//   const el = document.getElementById(tbodyId);
//   if (!el) return;
//   el.innerHTML = Array.from({ length: 5 }, () => `
//     <tr>${Array.from({ length: 7 }, () =>
//       `<td><div class="skeleton" style="height:16px; width:${60 + Math.random()*60 | 0}px;"></div></td>`
//     ).join('')}</tr>
//   `).join('');
// }

// function renderPagination(containerId, data, currentPage, onPage) {
//   const el = document.getElementById(containerId);
//   if (!el) return;

//   const totalPages = Math.ceil(data.total / (data.limit || 25));
//   if (totalPages <= 1) { el.innerHTML = ''; return; }

//   const prevDisabled = currentPage === 0;
//   const nextDisabled = currentPage >= totalPages - 1;

//   el.innerHTML = `
//     <div style="display:flex; align-items:center; gap:10px; justify-content:flex-end; padding:12px 14px; border-top:1px solid var(--border);">
//       <span style="font-size:12px; color:var(--text-muted);">
//         ${data.skip + 1}–${Math.min(data.skip + data.limit, data.total)} of ${data.total}
//       </span>
//       <button class="btn btn-ghost btn-sm" ${prevDisabled ? 'disabled' : ''} onclick="(${onPage.toString()})(${currentPage - 1})">
//         ← Prev
//       </button>
//       <button class="btn btn-ghost btn-sm" ${nextDisabled ? 'disabled' : ''} onclick="(${onPage.toString()})(${currentPage + 1})">
//         Next →
//       </button>
//     </div>`;
// }

// function updateConnectionStatus(status, label) {
//   const dot  = document.getElementById('status-dot');
//   const text = document.getElementById('status-text');
//   if (dot)  { dot.className = `status-dot ${status}`; }
//   if (text) { text.textContent = label || status; }
// }

// function updateLastRefresh() {
//   state.lastRefresh = new Date();
//   const el = document.getElementById('last-refresh');
//   if (el) el.textContent = `Refreshed ${relativeTime(state.lastRefresh.toISOString())}`;
// }

// let _refreshLabelTimer = null;
// function startRefreshLabelUpdater() {
//   clearInterval(_refreshLabelTimer);
//   _refreshLabelTimer = setInterval(() => {
//     if (!state.lastRefresh) return;
//     const el = document.getElementById('last-refresh');
//     if (el) el.textContent = `Refreshed ${relativeTime(state.lastRefresh.toISOString())}`;
//   }, 15_000);
// }

// /* Toast notifications */
// function toast(message, type = 'info') {
//   const container = document.getElementById('toast-container');
//   if (!container) return;

//   const icons = { error: '⚠', success: '✓', info: 'ℹ' };
//   const el    = document.createElement('div');
//   el.className = `toast ${type}`;
//   el.innerHTML = `<span>${icons[type] || icons.info}</span><span>${escHtml(message)}</span>`;
//   container.appendChild(el);

//   setTimeout(() => {
//     el.style.animation = 'toast-out 0.2s ease forwards';
//     setTimeout(() => el.remove(), 200);
//   }, 3500);
// }

// /* ═══════════════════════════════════════════════════════════════
//    7c. POLICIES TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshPolicies() {
//   if (state.loading.policies) return;
//   state.loading.policies = true;
//   setTableLoading('policies-tbody');
//   const skip  = state.filters.policiesPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchPolicies(skip, state.filters.pageSize);
//   state.loading.policies = false;
//   if (!ok) { toast(error || 'Failed to load policies', 'error'); return; }
//   state.data.policies = data;
//   renderPolicies(data);
// }

// function renderPolicies(data) {
//   const tbody   = document.getElementById('policies-tbody');
//   const counter = document.getElementById('policies-count');
//   if (!tbody) return;
//   if (counter) counter.textContent = data.total;

//   if (!data.policies || data.policies.length === 0) {
//     tbody.innerHTML = `<tr><td colspan="7"><div class="empty-state">
//       <span class="empty-icon">🛡</span>
//       <span class="empty-title">No policies defined yet</span>
//       <span class="empty-body">Create policies via POST /policies.</span>
//     </div></td></tr>`;
//     renderPagination('policies-pagination', data, state.filters.policiesPage, (p) => {
//       state.filters.policiesPage = p; refreshPolicies();
//     });
//     return;
//   }

//   const severityBadge = (sev) => {
//     const map = {
//       'LOW':      '<span class="badge" style="background:rgba(63,185,80,0.15);color:var(--accent-green);">LOW</span>',
//       'MEDIUM':   '<span class="badge" style="background:rgba(227,135,62,0.15);color:var(--accent-orange);">MEDIUM</span>',
//       'HIGH':     '<span class="badge" style="background:rgba(248,81,73,0.15);color:var(--accent-red);">HIGH</span>',
//       'CRITICAL': '<span class="badge" style="background:rgba(188,140,255,0.15);color:var(--accent-purple);">CRITICAL</span>',
//     };
//     return map[sev] || `<span class="badge">${escHtml(sev)}</span>`;
//   };

//   const ruleTypeBadge = (rt) => {
//     const labels = {
//       'tool_allow':   '✓ tool_allow',
//       'tool_deny':    '⛔ tool_deny',
//       'rate_limit':   '⏱ rate_limit',
//       'prompt_guard': '🔍 prompt_guard',
//       'time_window':  '🕐 time_window',
//     };
//     return `<span class="badge badge-tool">${escHtml(labels[rt] || rt)}</span>`;
//   };

//   tbody.innerHTML = data.policies.map(p => `
//     <tr>
//       <td><span style="font-weight:600;">${escHtml(p.name)}</span>
//         ${p.description ? `<br><span class="td-dim" style="font-size:11px;">${escHtml(p.description.slice(0,50))}</span>` : ''}
//       </td>
//       <td>${ruleTypeBadge(p.rule_type)}</td>
//       <td>${severityBadge(p.severity)}</td>
//       <td>${p.is_active
//           ? '<span class="badge badge-permitted">✓ active</span>'
//           : '<span class="badge badge-run-end">○ inactive</span>'}</td>
//       <td><span class="td-mono" style="font-size:12px;">${p.agent_count}</span></td>
//       <td><div class="td-truncate td-mono" style="font-size:11px;max-width:180px;color:var(--text-secondary);">
//         ${escHtml(JSON.stringify(p.rule_config))}
//       </div></td>
//       <td class="td-mono td-dim">${relativeTime(p.created_at)}</td>
//     </tr>`).join('');

//   renderPagination('policies-pagination', data, state.filters.policiesPage, (p) => {
//     state.filters.policiesPage = p; refreshPolicies();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    9. AUTO-REFRESH + INIT
// ═══════════════════════════════════════════════════════════════ */

// const REFRESH_INTERVAL_MS = 30_000;

// function refreshCurrentView() {
//   if (!state.token) return;

//   const btn = document.getElementById('btn-refresh');
//   if (btn) {
//     btn.classList.add('btn-spinning');
//     btn.querySelector('.btn-icon').style.animation = 'spin 0.8s linear infinite';
//   }

//   const views = {
//     overview:     refreshOverview,
//     violations:   refreshViolations,
//     audit:        refreshAuditLog,
//     interactions: refreshInteractions,
//     policies:     refreshPolicies,
//   };
//   const fn = views[state.currentView];
//   if (fn) fn().finally(() => {
//     updateLastRefresh();
//     if (btn) btn.querySelector('.btn-icon').style.animation = '';
//   });
// }

// function startAutoRefresh() {
//   stopAutoRefresh();
//   state.refreshTimer = setInterval(refreshCurrentView, REFRESH_INTERVAL_MS);
// }

// function stopAutoRefresh() {
//   if (state.refreshTimer) clearInterval(state.refreshTimer);
//   state.refreshTimer = null;
// }

// /* ── Boot ─────────────────────────────────────────────────────── */
// document.addEventListener('DOMContentLoaded', () => {

//   // Nav click handlers
//   document.querySelectorAll('.nav-item[data-view]').forEach(el => {
//     el.addEventListener('click', () => navigateTo(el.dataset.view));
//   });

//   // Refresh button
//   document.getElementById('btn-refresh')?.addEventListener('click', refreshCurrentView);

//   // Disconnect button
//   document.getElementById('btn-disconnect')?.addEventListener('click', () => {
//     clearToken();
//     toast('Disconnected', 'info');
//   });

//   // Register form
//   document.getElementById('btn-register')?.addEventListener('click', handleRegister);

//   // Paste token
//   document.getElementById('btn-use-token')?.addEventListener('click', handlePasteToken);

//   // Audit event type filter
//   document.getElementById('audit-filter-type')?.addEventListener('change', (e) => {
//     state.filters.auditEventType = e.target.value;
//     state.filters.auditPage      = 0;
//     refreshAuditLog();
//   });

//   // Mobile sidebar toggle
//   document.getElementById('mobile-menu-btn')?.addEventListener('click', () => {
//     document.querySelector('.sidebar')?.classList.toggle('open');
//   });

//   // Start relative-time label updater
//   startRefreshLabelUpdater();

//   // Restore session or show auth banner
//   if (!tryRestoreSession()) {
//     showAuthBanner();
//     updateConnectionStatus('disconnected', 'Not connected');
//   }
// });



/**
 * app.js — AgentWatch Dashboard
 *
 * Architecture:
 *   State → API → Render pipeline.
 *   No framework, no bundler.  Vanilla ES2022 modules pattern
 *   (everything in one file for simplicity — split if the project grows).
 *
 * Sections:
 *   1. State management
 *   2. API client (all fetch calls, token injection)
 *   3. Navigation / view router
 *   4. Auth flow (register / paste token)
 *   5. Render: Overview (stats + latency)
 *   6. Render: Violations table
 *   7. Render: Audit log table
 *   8. Utilities (badges, timestamps, toast)
 *   9. Auto-refresh + init
 */

// 'use strict';

// /* ═══════════════════════════════════════════════════════════════
//    1. STATE
// ═══════════════════════════════════════════════════════════════ */

// const state = {
//   token:           null,       // JWT bearer token
//   agentId:         null,       // UUID of the authenticated agent
//   agentName:       null,
//   lastRefresh:     null,
//   refreshTimer:    null,
//   currentView:     'overview',
//   loading: {
//     stats:        false,
//     violations:   false,
//     audit:        false,
//     interactions: false,
//     policies:     false,
//   },
//   data: {
//     stats:        null,
//     violations:   null,    // { violations: [], total, skip, limit }
//     audit:        null,    // { events: [], total, skip, limit }
//     interactions: null,    // { interactions: [], total, skip, limit }
//     policies:     null,    // { policies: [], total, skip, limit }
//   },
//   filters: {
//     auditEventType:    '',
//     violationPage:     0,
//     auditPage:         0,
//     interactionsPage:  0,
//     policiesPage:      0,
//     pageSize:          25,
//   },
// };

// /* ═══════════════════════════════════════════════════════════════
//    2. API CLIENT
// ═══════════════════════════════════════════════════════════════ */

// const API_BASE = '';  // Same origin — served by FastAPI at /dashboard/

// function authHeaders() {
//   return {
//     'Content-Type': 'application/json',
//     ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
//   };
// }

// /**
//  * Generic fetch wrapper.
//  * Returns { ok: true, data } or { ok: false, error, status }.
//  */
// async function apiFetch(path, opts = {}) {
//   try {
//     const res = await fetch(`${API_BASE}${path}`, {
//       ...opts,
//       headers: { ...authHeaders(), ...(opts.headers || {}) },
//     });
//     const data = await res.json().catch(() => null);
//     if (!res.ok) {
//       return { ok: false, error: data?.detail || `HTTP ${res.status}`, status: res.status };
//     }
//     return { ok: true, data };
//   } catch (err) {
//     return { ok: false, error: err.message || 'Network error', status: 0 };
//   }
// }

// async function fetchStats() {
//   return apiFetch('/analytics/stats');
// }

// async function fetchViolations(skip = 0, limit = 25) {
//   return apiFetch(`/governance/violations?skip=${skip}&limit=${limit}`);
// }

// async function fetchInteractions(skip = 0, limit = 25) {
//   return apiFetch(`/agent-interactions?skip=${skip}&limit=${limit}`);
// }

// async function fetchTrust() {
//   return apiFetch('/analytics/trust');
// }

// async function fetchAgentTrust(agentId) {
//   return apiFetch(`/analytics/trust/${agentId}`);
// }

// async function fetchAuditLogs(skip = 0, limit = 25, eventType = '') {
//   const et = eventType ? `&event_type=${encodeURIComponent(eventType)}` : '';
//   return apiFetch(`/audit/logs?skip=${skip}&limit=${limit}${et}`);
// }

// async function fetchPolicies(skip = 0, limit = 25) {
//   return apiFetch(`/policies?skip=${skip}&limit=${limit}`);
// }

// async function registerAgent(name, secret, tools) {
//   return apiFetch('/agents/register', {
//     method: 'POST',
//     body: JSON.stringify({ name, secret, allowed_tools: tools }),
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    3. NAVIGATION
// ═══════════════════════════════════════════════════════════════ */

// function navigateTo(viewId) {
//   state.currentView = viewId;

//   // Update sidebar active state
//   document.querySelectorAll('.nav-item').forEach(el => {
//     el.classList.toggle('active', el.dataset.view === viewId);
//   });

//   // Swap visible view
//   document.querySelectorAll('.view').forEach(el => {
//     el.classList.toggle('active', el.id === `view-${viewId}`);
//   });

//   // Update topbar title
//   const titles = {
//     overview:     { title: 'Overview',              sub: 'Platform health at a glance' },
//     violations:   { title: 'Governance Violations', sub: 'Blocked tool calls and policy enforcement' },
//     audit:        { title: 'Audit Log',             sub: 'Full event trace across all agent runs' },
//     interactions: { title: 'Agent Interactions',    sub: 'Agent-to-agent communication records' },
//     policies:     { title: 'Governance Policies',   sub: 'Named rules enforced before every run' },
//   };
//   const t = titles[viewId] || {};
//   document.getElementById('topbar-title').textContent    = t.title || '';
//   document.getElementById('topbar-subtitle').textContent = t.sub   || '';

//   // Load data for the view
//   if (!state.token) return;
//   if (viewId === 'overview')     refreshOverview();
//   if (viewId === 'violations')   refreshViolations();
//   if (viewId === 'audit')        refreshAuditLog();
//   if (viewId === 'interactions') refreshInteractions();
//   if (viewId === 'policies')     refreshPolicies();
// }

// /* ═══════════════════════════════════════════════════════════════
//    4. AUTH FLOW
// ═══════════════════════════════════════════════════════════════ */

// function setToken(token, agentId, agentName) {
//   state.token     = token;
//   state.agentId   = agentId;
//   state.agentName = agentName || 'unknown';

//   // Persist in sessionStorage (not localStorage — clears on tab close)
//   sessionStorage.setItem('aw_token',      token);
//   sessionStorage.setItem('aw_agent_id',   agentId);
//   sessionStorage.setItem('aw_agent_name', agentName || '');

//   updateConnectionStatus('connected', agentName || agentId?.slice(0, 8) + '…');
//   hideAuthBanner();
//   startAutoRefresh();
//   navigateTo(state.currentView);
// }

// function clearToken() {
//   state.token = state.agentId = state.agentName = null;
//   sessionStorage.removeItem('aw_token');
//   sessionStorage.removeItem('aw_agent_id');
//   sessionStorage.removeItem('aw_agent_name');
//   stopAutoRefresh();
//   updateConnectionStatus('disconnected', 'Not connected');
//   showAuthBanner();
// }

// function tryRestoreSession() {
//   const token     = sessionStorage.getItem('aw_token');
//   const agentId   = sessionStorage.getItem('aw_agent_id');
//   const agentName = sessionStorage.getItem('aw_agent_name');
//   if (token && agentId) {
//     setToken(token, agentId, agentName);
//     return true;
//   }
//   return false;
// }

// function showAuthBanner() {
//   document.getElementById('auth-banner').style.display = 'block';
// }

// function hideAuthBanner() {
//   document.getElementById('auth-banner').style.display = 'none';
// }

// async function handleRegister() {
//   const name   = document.getElementById('reg-name').value.trim();
//   const secret = document.getElementById('reg-secret').value;
//   const tools  = Array.from(
//     document.querySelectorAll('.tool-checkbox:checked')
//   ).map(el => el.value);

//   if (!name || name.length < 2) {
//     return toast('Agent name must be at least 2 characters', 'error');
//   }
//   if (!secret || secret.length < 12) {
//     return toast('Secret must be at least 12 characters', 'error');
//   }

//   const btn = document.getElementById('btn-register');
//   btn.disabled = true;
//   btn.innerHTML = '<span class="btn-icon btn-spinning">⟳</span> Registering…';

//   const { ok, data, error } = await registerAgent(name, secret, tools);

//   btn.disabled = false;
//   btn.innerHTML = '🚀 Register Agent';

//   if (!ok) {
//     toast(error || 'Registration failed', 'error');
//     return;
//   }

//   toast(`Agent "${name}" registered successfully!`, 'success');
//   setToken(data.access_token, data.agent_id, name);
//   document.getElementById('token-display').value = data.access_token;
// }

// async function handlePasteToken() {
//   const raw = document.getElementById('token-display').value.trim();
//   if (!raw) return toast('Paste a JWT token first', 'error');

//   // Decode payload to extract sub (agent_id) and agent_name — no library needed
//   try {
//     const parts = raw.split('.');
//     if (parts.length !== 3) throw new Error('Not a JWT');
//     const payload = JSON.parse(atob(parts[1].replace(/-/g,'+').replace(/_/g,'/')));
//     if (!payload.sub) throw new Error('Token has no sub claim');
//     setToken(raw, payload.sub, payload.agent_name || payload.sub.slice(0, 8) + '…');
//     toast('Token applied successfully', 'success');
//   } catch (e) {
//     toast(`Invalid token: ${e.message}`, 'error');
//   }
// }

// /* ═══════════════════════════════════════════════════════════════
//    5. OVERVIEW — stats cards + latency bars
// ═══════════════════════════════════════════════════════════════ */

// async function refreshOverview() {
//   if (state.loading.stats) return;
//   state.loading.stats = true;

//   // Show skeleton values while loading
//   renderStatSkeletons();

//   const { ok, data, error } = await fetchStats();
//   state.loading.stats = false;

//   if (!ok) {
//     toast(error || 'Failed to load stats', 'error');
//     renderStatsError();
//     return;
//   }

//   state.data.stats = data;
//   renderStats(data);
//   renderLatency(data.tool_latency || []);

//   // Fetch the 5 most recent violations for the overview preview panel.
//   // Separate lightweight call (limit=5) made only when overview is active.
//   // renderViolations on the Violations page is never involved.
//   const vRes = await fetchViolations(0, 5);
//   if (vRes.ok) renderOverviewViolations(vRes.data);
// }

// function renderStatSkeletons() {
//   const ids = ['stat-agents','stat-runs','stat-events','stat-violations','stat-rate','stat-completed','stat-avg-trust'];
//   ids.forEach(id => {
//     const el = document.getElementById(id);
//     if (el) el.innerHTML = '<span class="skeleton" style="display:inline-block;width:48px;height:28px;"></span>';
//   });
// }

// function renderStatsError() {
//   const ids = ['stat-agents','stat-runs','stat-events','stat-violations','stat-rate','stat-completed','stat-avg-trust'];
//   ids.forEach(id => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = '—';
//   });
// }

// function renderStats(s) {
//   const set = (id, val) => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = val;
//   };

//   set('stat-agents',    s.total_agents);
//   set('stat-runs',      s.total_runs);
//   set('stat-events',    s.total_events);
//   set('stat-violations', s.total_violations);
//   set('stat-rate',      `${s.violation_rate.toFixed(1)}%`);
//   set('stat-completed', s.completed_runs);

//   // Sub-lines
//   const setSub = (id, val) => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = val;
//   };
//   setSub('stat-runs-sub',       `${s.completed_runs} completed · ${s.failed_runs} failed`);
//   setSub('stat-violations-sub', s.total_tool_calls > 0
//     ? `out of ${s.total_tool_calls} tool calls`
//     : 'no tool calls yet');

//   // Trust score aggregates
//   if (s.average_trust_score !== undefined) {
//     set('stat-avg-trust', s.average_trust_score.toFixed(1));
//     const dist = s.trust_distribution || {};
//     const distParts = Object.entries(dist)
//       .filter(([,v]) => v > 0)
//       .map(([k, v]) => `${v} ${k}`)
//       .join(' · ');
//     setSub('stat-avg-trust-sub', distParts || 'no agents yet');
//   }
// }

// function renderLatency(tools) {
//   const grid = document.getElementById('latency-grid');
//   if (!grid) return;

//   if (!tools || tools.length === 0) {
//     grid.innerHTML = `
//       <div class="empty-state" style="padding:24px; grid-column:1/-1;">
//         <span class="empty-icon">⏱</span>
//         <span class="empty-title">No tool executions yet</span>
//         <span class="empty-body">Run an agent with a prompt to see latency data here.</span>
//       </div>`;
//     return;
//   }

//   const maxMs = Math.max(...tools.map(t => t.p95_ms || t.avg_ms || 1), 1);

//   grid.innerHTML = tools.map(t => {
//     const avgPct = Math.min(100, ((t.avg_ms || 0) / maxMs) * 100).toFixed(1);
//     const p95Pct = Math.min(100, ((t.p95_ms || 0) / maxMs) * 100).toFixed(1);

//     return `
//       <div class="latency-card">
//         <div class="latency-header">
//           <span class="latency-tool-name">${escHtml(t.tool_name)}</span>
//           <span class="latency-calls">${t.call_count} calls</span>
//         </div>
//         <div class="latency-bar-row">
//           <span class="latency-bar-label">avg</span>
//           <div class="latency-bar-track">
//             <div class="latency-bar-fill" style="width:${avgPct}%"></div>
//           </div>
//           <span class="latency-val">${t.avg_ms != null ? t.avg_ms.toFixed(1) + 'ms' : '—'}</span>
//         </div>
//         <div class="latency-bar-row">
//           <span class="latency-bar-label">p95</span>
//           <div class="latency-bar-track">
//             <div class="latency-bar-fill p95" style="width:${p95Pct}%"></div>
//           </div>
//           <span class="latency-val">${t.p95_ms != null ? t.p95_ms.toFixed(1) + 'ms' : '—'}</span>
//         </div>
//       </div>`;
//   }).join('');
// }

// /* ═══════════════════════════════════════════════════════════════
//    5b. OVERVIEW VIOLATIONS PREVIEW
//    Renders the 5-row preview panel on the Overview page.
//    Uses data already fetched by refreshOverview() — no extra API call.
//    Completely independent of renderViolations(); neither calls the other.
// ═══════════════════════════════════════════════════════════════ */

// function renderOverviewViolations(data) {
//   const tbody   = document.getElementById('overview-violations-tbody');
//   const counter = document.getElementById('overview-violations-count');
//   if (!tbody) return;

//   if (counter) counter.textContent = data.total;

//   if (!data.violations || data.violations.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="5">
//         <div class="empty-state" style="padding:20px;">
//           <span class="empty-icon">✅</span>
//           <span class="empty-title">No violations yet</span>
//         </div>
//       </td></tr>`;
//     return;
//   }

//   tbody.innerHTML = data.violations.slice(0, 5).map(v => {
//     const inputStr = v.attempted_input
//       ? JSON.stringify(v.attempted_input).slice(0, 50)
//       : '—';
//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(v.timestamp)}</td>
//         <td>${escHtml(v.agent_name || '—')}</td>
//         <td><span class="badge badge-tool">${escHtml(v.tool_name || '—')}</span></td>
//         <td>
//           <span class="violation-input"
//                 style="font-family:var(--font-mono);font-size:11.5px;">
//             ${escHtml(inputStr)}
//           </span>
//         </td>
//         <td style="font-size:12px;color:var(--text-secondary);
//                    max-width:180px;overflow:hidden;
//                    text-overflow:ellipsis;white-space:nowrap;">
//           ${escHtml((v.denial_message || '').slice(0, 60))}
//         </td>
//       </tr>`;
//   }).join('');
// }

// /* ═══════════════════════════════════════════════════════════════
//    6. VIOLATIONS TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshViolations() {
//   if (state.loading.violations) return;
//   state.loading.violations = true;
//   setTableLoading('violations-tbody');

//   const skip  = state.filters.violationPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchViolations(skip, state.filters.pageSize);
//   state.loading.violations = false;

//   if (!ok) {
//     toast(error || 'Failed to load violations', 'error');
//     return;
//   }

//   state.data.violations = data;
//   renderViolations(data);
// }

// function renderViolations(data) {
//   const tbody   = document.getElementById('violations-tbody');
//   const counter = document.getElementById('violations-count');
//   if (counter) counter.textContent = data.total;

//   if (!data.violations || data.violations.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="6">
//         <div class="empty-state">
//           <span class="empty-icon">✅</span>
//           <span class="empty-title">No governance violations</span>
//           <span class="empty-body">All tool calls are within permitted bounds. Register an agent with limited tools and run a prompt to see violations here.</span>
//         </div>
//       </td></tr>`;
//     renderPagination('violations-pagination', data, state.filters.violationPage, (p) => {
//       state.filters.violationPage = p;
//       refreshViolations();
//     });
//     return;
//   }

//   tbody.innerHTML = data.violations.map(v => {
//     const inputStr = v.attempted_input
//       ? JSON.stringify(v.attempted_input)
//       : '—';

//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(v.timestamp)}</td>
//         <td>
//           <span class="badge badge-violation">⛔ violation</span>
//         </td>
//         <td>
//           <span style="font-weight:500;">${escHtml(v.agent_name || '—')}</span>
//           <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(v.agent_id.slice(0,8))}…</span>
//         </td>
//         <td><span class="badge badge-tool">${escHtml(v.tool_name || '—')}</span></td>
//         <td>
//           <div class="violation-detail">
//             <span class="violation-input">${escHtml(inputStr)}</span>
//           </div>
//         </td>
//         <td>
//           <div style="max-width:220px; font-size:12px; color:var(--text-secondary); line-height:1.5;">
//             ${escHtml(v.denial_message || '—')}
//           </div>
//         </td>
//       </tr>`;
//   }).join('');

//   renderPagination('violations-pagination', data, state.filters.violationPage, (p) => {
//     state.filters.violationPage = p;
//     refreshViolations();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    7. AUDIT LOG TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshAuditLog() {
//   if (state.loading.audit) return;
//   state.loading.audit = true;
//   setTableLoading('audit-tbody');

//   const skip = state.filters.auditPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchAuditLogs(
//     skip,
//     state.filters.pageSize,
//     state.filters.auditEventType,
//   );
//   state.loading.audit = false;

//   if (!ok) {
//     toast(error || 'Failed to load audit log', 'error');
//     return;
//   }

//   state.data.audit = data;
//   renderAuditLog(data);
// }

// function renderAuditLog(data) {
//   const tbody   = document.getElementById('audit-tbody');
//   const counter = document.getElementById('audit-count');
//   if (counter) counter.textContent = data.total;

//   if (!data.events || data.events.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="7">
//         <div class="empty-state">
//           <span class="empty-icon">📋</span>
//           <span class="empty-title">No audit events</span>
//           <span class="empty-body">Submit a run via POST /agents/run to populate the audit log.</span>
//         </div>
//       </td></tr>`;
//     renderPagination('audit-pagination', data, state.filters.auditPage, (p) => {
//       state.filters.auditPage = p;
//       refreshAuditLog();
//     });
//     return;
//   }

//   tbody.innerHTML = data.events.map(e => {
//     const inputSummary = e.input_data
//       ? JSON.stringify(e.input_data).slice(0, 60) + (JSON.stringify(e.input_data).length > 60 ? '…' : '')
//       : '—';

//     const outputSummary = e.output_data
//       ? JSON.stringify(e.output_data).slice(0, 60) + (JSON.stringify(e.output_data).length > 60 ? '…' : '')
//       : '—';

//     const permittedBadge = e.permitted === true
//       ? '<span class="badge badge-permitted">✓ allowed</span>'
//       : e.permitted === false
//         ? '<span class="badge badge-blocked">⛔ blocked</span>'
//         : '<span style="color:var(--text-muted);font-size:12px;">—</span>';

//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(e.timestamp)}</td>
//         <td>${eventTypeBadge(e.event_type)}</td>
//         <td>
//           <span style="font-weight:500;">${escHtml(e.agent_name || '—')}</span>
//         </td>
//         <td>
//           ${e.tool_name
//             ? `<span class="badge badge-tool">${escHtml(e.tool_name)}</span>`
//             : '<span class="td-dim">—</span>'
//           }
//         </td>
//         <td>${permittedBadge}</td>
//         <td>
//           <div class="td-truncate td-mono" style="font-size:11.5px; color:var(--text-secondary);">
//             ${escHtml(inputSummary)}
//           </div>
//         </td>
//         <td>
//           ${e.latency_ms != null
//             ? `<span class="td-mono" style="font-size:12px; color:var(--accent-teal);">${e.latency_ms.toFixed(1)}ms</span>`
//             : '<span class="td-dim">—</span>'
//           }
//         </td>
//       </tr>`;
//   }).join('');

//   renderPagination('audit-pagination', data, state.filters.auditPage, (p) => {
//     state.filters.auditPage = p;
//     refreshAuditLog();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    7b. AGENT INTERACTIONS TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshInteractions() {
//   if (state.loading.interactions) return;
//   state.loading.interactions = true;
//   setTableLoading('interactions-tbody');

//   const skip  = state.filters.interactionsPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchInteractions(skip, state.filters.pageSize);
//   state.loading.interactions = false;

//   if (!ok) {
//     toast(error || 'Failed to load interactions', 'error');
//     return;
//   }

//   state.data.interactions = data;
//   renderInteractions(data);
// }

// function renderInteractions(data) {
//   const tbody   = document.getElementById('interactions-tbody');
//   const counter = document.getElementById('interactions-count');
//   if (counter) counter.textContent = data.total;

//   if (!data.interactions || data.interactions.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="5">
//         <div class="empty-state">
//           <span class="empty-icon">🔗</span>
//           <span class="empty-title">No agent interactions yet</span>
//           <span class="empty-body">
//             Use POST /agent-interactions to record a handoff, delegation,
//             request, or response between two registered agents.
//           </span>
//         </div>
//       </td></tr>`;
//     renderPagination('interactions-pagination', data, state.filters.interactionsPage, (p) => {
//       state.filters.interactionsPage = p;
//       refreshInteractions();
//     });
//     return;
//   }

//   const typeBadge = (type) => {
//     const map = {
//       'handoff':    '<span class="badge badge-handoff">⇒ handoff</span>',
//       'delegation': '<span class="badge badge-delegation">↓ delegation</span>',
//       'request':    '<span class="badge badge-request">? request</span>',
//       'response':   '<span class="badge badge-response">✓ response</span>',
//     };
//     return map[type] || `<span class="badge">${escHtml(type)}</span>`;
//   };

//   tbody.innerHTML = data.interactions.map(i => `
//     <tr>
//       <td class="td-mono td-dim">${relativeTime(i.created_at)}</td>
//       <td>${typeBadge(i.interaction_type)}</td>
//       <td>
//         <span style="font-weight:500;">${escHtml(i.source_agent_name || '—')}</span>
//         <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(i.source_agent_id.slice(0,8))}…</span>
//       </td>
//       <td>
//         <span style="font-weight:500;">${escHtml(i.target_agent_name || '—')}</span>
//         <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(i.target_agent_id.slice(0,8))}…</span>
//       </td>
//       <td>
//         <div style="max-width:260px; font-size:12px; color:var(--text-secondary);
//                     overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
//           ${i.message ? escHtml(i.message) : '<span class="td-dim">—</span>'}
//         </div>
//       </td>
//     </tr>`).join('');

//   renderPagination('interactions-pagination', data, state.filters.interactionsPage, (p) => {
//     state.filters.interactionsPage = p;
//     refreshInteractions();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    8. UTILITIES
// ═══════════════════════════════════════════════════════════════ */

// function escHtml(str) {
//   if (str == null) return '';
//   return String(str)
//     .replace(/&/g, '&amp;')
//     .replace(/</g, '&lt;')
//     .replace(/>/g, '&gt;')
//     .replace(/"/g, '&quot;');
// }

// function relativeTime(isoString) {
//   if (!isoString) return '—';
//   const date  = new Date(isoString.endsWith('Z') ? isoString : isoString + 'Z');
//   const diff  = (Date.now() - date.getTime()) / 1000;
//   if (diff < 5)   return 'just now';
//   if (diff < 60)  return `${Math.floor(diff)}s ago`;
//   if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
//   if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
//   return date.toLocaleDateString();
// }

// function eventTypeBadge(type) {
//   const map = {
//     'run_start': '<span class="badge badge-run-start">▶ run_start</span>',
//     'tool_call': '<span class="badge badge-tool-call">⚙ tool_call</span>',
//     'tool_end':  '<span class="badge badge-tool-end">✓ tool_end</span>',
//     'violation': '<span class="badge badge-violation">⛔ violation</span>',
//     'run_end':   '<span class="badge badge-run-end">■ run_end</span>',
//     'agent_handoff':    '<span class="badge badge-handoff">⇒ agent_handoff</span>',
//     'policy_violation': '<span class="badge badge-violation">🛡 policy_violation</span>',
//   };
//   return map[type] || `<span class="badge">${escHtml(type)}</span>`;
// }

// function trustLevelBadge(level) {
//   const map = {
//     'TRUSTED':   '<span class="badge" style="background:rgba(63,185,80,0.15);color:#3fb950;">🏅 TRUSTED</span>',
//     'MONITORED': '<span class="badge" style="background:rgba(79,142,247,0.15);color:#4f8ef7;">👁 MONITORED</span>',
//     'WARNING':   '<span class="badge" style="background:rgba(227,135,62,0.15);color:#e3873e;">⚠ WARNING</span>',
//     'HIGH_RISK': '<span class="badge" style="background:rgba(248,81,73,0.15);color:#f85149;">🔴 HIGH_RISK</span>',
//   };
//   return map[level] || `<span class="badge">${escHtml(level)}</span>`;
// }

// function formatTrustScore(score) {
//   if (score === null || score === undefined) return '—';
//   const color = score >= 90 ? '#3fb950'
//               : score >= 70 ? '#4f8ef7'
//               : score >= 50 ? '#e3873e'
//               : '#f85149';
//   return `<span style="font-family:var(--font-mono);font-weight:700;color:${color};">${score.toFixed(1)}</span>`;
// }

// function setTableLoading(tbodyId) {
//   const el = document.getElementById(tbodyId);
//   if (!el) return;
//   el.innerHTML = Array.from({ length: 5 }, () => `
//     <tr>${Array.from({ length: 7 }, () =>
//       `<td><div class="skeleton" style="height:16px; width:${60 + Math.random()*60 | 0}px;"></div></td>`
//     ).join('')}</tr>
//   `).join('');
// }

// function renderPagination(containerId, data, currentPage, onPage) {
//   const el = document.getElementById(containerId);
//   if (!el) return;

//   const totalPages = Math.ceil(data.total / (data.limit || 25));
//   if (totalPages <= 1) { el.innerHTML = ''; return; }

//   const prevDisabled = currentPage === 0;
//   const nextDisabled = currentPage >= totalPages - 1;

//   el.innerHTML = `
//     <div style="display:flex; align-items:center; gap:10px; justify-content:flex-end; padding:12px 14px; border-top:1px solid var(--border);">
//       <span style="font-size:12px; color:var(--text-muted);">
//         ${data.skip + 1}–${Math.min(data.skip + data.limit, data.total)} of ${data.total}
//       </span>
//       <button class="btn btn-ghost btn-sm" ${prevDisabled ? 'disabled' : ''} onclick="(${onPage.toString()})(${currentPage - 1})">
//         ← Prev
//       </button>
//       <button class="btn btn-ghost btn-sm" ${nextDisabled ? 'disabled' : ''} onclick="(${onPage.toString()})(${currentPage + 1})">
//         Next →
//       </button>
//     </div>`;
// }

// function updateConnectionStatus(status, label) {
//   const dot  = document.getElementById('status-dot');
//   const text = document.getElementById('status-text');
//   if (dot)  { dot.className = `status-dot ${status}`; }
//   if (text) { text.textContent = label || status; }
// }

// function updateLastRefresh() {
//   state.lastRefresh = new Date();
//   const el = document.getElementById('last-refresh');
//   if (el) el.textContent = `Refreshed ${relativeTime(state.lastRefresh.toISOString())}`;
// }

// let _refreshLabelTimer = null;
// function startRefreshLabelUpdater() {
//   clearInterval(_refreshLabelTimer);
//   _refreshLabelTimer = setInterval(() => {
//     if (!state.lastRefresh) return;
//     const el = document.getElementById('last-refresh');
//     if (el) el.textContent = `Refreshed ${relativeTime(state.lastRefresh.toISOString())}`;
//   }, 15_000);
// }

// /* Toast notifications */
// function toast(message, type = 'info') {
//   const container = document.getElementById('toast-container');
//   if (!container) return;

//   const icons = { error: '⚠', success: '✓', info: 'ℹ' };
//   const el    = document.createElement('div');
//   el.className = `toast ${type}`;
//   el.innerHTML = `<span>${icons[type] || icons.info}</span><span>${escHtml(message)}</span>`;
//   container.appendChild(el);

//   setTimeout(() => {
//     el.style.animation = 'toast-out 0.2s ease forwards';
//     setTimeout(() => el.remove(), 200);
//   }, 3500);
// }

// /* ═══════════════════════════════════════════════════════════════
//    7c. POLICIES TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshPolicies() {
//   if (state.loading.policies) return;
//   state.loading.policies = true;
//   setTableLoading('policies-tbody');
//   const skip  = state.filters.policiesPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchPolicies(skip, state.filters.pageSize);
//   state.loading.policies = false;
//   if (!ok) { toast(error || 'Failed to load policies', 'error'); return; }
//   state.data.policies = data;
//   renderPolicies(data);
// }

// function renderPolicies(data) {
//   const tbody   = document.getElementById('policies-tbody');
//   const counter = document.getElementById('policies-count');
//   if (!tbody) return;
//   if (counter) counter.textContent = data.total;

//   if (!data.policies || data.policies.length === 0) {
//     tbody.innerHTML = `<tr><td colspan="7"><div class="empty-state">
//       <span class="empty-icon">🛡</span>
//       <span class="empty-title">No policies defined yet</span>
//       <span class="empty-body">Create policies via POST /policies.</span>
//     </div></td></tr>`;
//     renderPagination('policies-pagination', data, state.filters.policiesPage, (p) => {
//       state.filters.policiesPage = p; refreshPolicies();
//     });
//     return;
//   }

//   const severityBadge = (sev) => {
//     const map = {
//       'LOW':      '<span class="badge" style="background:rgba(63,185,80,0.15);color:var(--accent-green);">LOW</span>',
//       'MEDIUM':   '<span class="badge" style="background:rgba(227,135,62,0.15);color:var(--accent-orange);">MEDIUM</span>',
//       'HIGH':     '<span class="badge" style="background:rgba(248,81,73,0.15);color:var(--accent-red);">HIGH</span>',
//       'CRITICAL': '<span class="badge" style="background:rgba(188,140,255,0.15);color:var(--accent-purple);">CRITICAL</span>',
//     };
//     return map[sev] || `<span class="badge">${escHtml(sev)}</span>`;
//   };

//   const ruleTypeBadge = (rt) => {
//     const labels = {
//       'tool_allow':   '✓ tool_allow',
//       'tool_deny':    '⛔ tool_deny',
//       'rate_limit':   '⏱ rate_limit',
//       'prompt_guard': '🔍 prompt_guard',
//       'time_window':  '🕐 time_window',
//     };
//     return `<span class="badge badge-tool">${escHtml(labels[rt] || rt)}</span>`;
//   };

//   tbody.innerHTML = data.policies.map(p => `
//     <tr>
//       <td><span style="font-weight:600;">${escHtml(p.name)}</span>
//         ${p.description ? `<br><span class="td-dim" style="font-size:11px;">${escHtml(p.description.slice(0,50))}</span>` : ''}
//       </td>
//       <td>${ruleTypeBadge(p.rule_type)}</td>
//       <td>${severityBadge(p.severity)}</td>
//       <td>${p.is_active
//           ? '<span class="badge badge-permitted">✓ active</span>'
//           : '<span class="badge badge-run-end">○ inactive</span>'}</td>
//       <td><span class="td-mono" style="font-size:12px;">${p.agent_count}</span></td>
//       <td><div class="td-truncate td-mono" style="font-size:11px;max-width:180px;color:var(--text-secondary);">
//         ${escHtml(JSON.stringify(p.rule_config))}
//       </div></td>
//       <td class="td-mono td-dim">${relativeTime(p.created_at)}</td>
//     </tr>`).join('');

//   renderPagination('policies-pagination', data, state.filters.policiesPage, (p) => {
//     state.filters.policiesPage = p; refreshPolicies();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    9. AUTO-REFRESH + INIT
// ═══════════════════════════════════════════════════════════════ */

// const REFRESH_INTERVAL_MS = 30_000;

// function refreshCurrentView() {
//   if (!state.token) return;

//   const btn = document.getElementById('btn-refresh');
//   if (btn) {
//     btn.classList.add('btn-spinning');
//     btn.querySelector('.btn-icon').style.animation = 'spin 0.8s linear infinite';
//   }

//   const views = {
//     overview:     refreshOverview,
//     violations:   refreshViolations,
//     audit:        refreshAuditLog,
//     interactions: refreshInteractions,
//     policies:     refreshPolicies,
//   };
//   const fn = views[state.currentView];
//   if (fn) fn().finally(() => {
//     updateLastRefresh();
//     if (btn) btn.querySelector('.btn-icon').style.animation = '';
//   });
// }

// function startAutoRefresh() {
//   stopAutoRefresh();
//   state.refreshTimer = setInterval(refreshCurrentView, REFRESH_INTERVAL_MS);
// }

// function stopAutoRefresh() {
//   if (state.refreshTimer) clearInterval(state.refreshTimer);
//   state.refreshTimer = null;
// }

// /* ── Boot ─────────────────────────────────────────────────────── */
// document.addEventListener('DOMContentLoaded', () => {

//   // Nav click handlers
//   document.querySelectorAll('.nav-item[data-view]').forEach(el => {
//     el.addEventListener('click', () => navigateTo(el.dataset.view));
//   });

//   // Refresh button
//   document.getElementById('btn-refresh')?.addEventListener('click', refreshCurrentView);

//   // Disconnect button
//   document.getElementById('btn-disconnect')?.addEventListener('click', () => {
//     clearToken();
//     toast('Disconnected', 'info');
//   });

//   // Register form
//   document.getElementById('btn-register')?.addEventListener('click', handleRegister);

//   // Paste token
//   document.getElementById('btn-use-token')?.addEventListener('click', handlePasteToken);

//   // Audit event type filter
//   document.getElementById('audit-filter-type')?.addEventListener('change', (e) => {
//     state.filters.auditEventType = e.target.value;
//     state.filters.auditPage      = 0;
//     refreshAuditLog();
//   });

//   // Mobile sidebar toggle
//   document.getElementById('mobile-menu-btn')?.addEventListener('click', () => {
//     document.querySelector('.sidebar')?.classList.toggle('open');
//   });

//   // Start relative-time label updater
//   startRefreshLabelUpdater();

//   // Restore session or show auth banner
//   if (!tryRestoreSession()) {
//     showAuthBanner();
//     updateConnectionStatus('disconnected', 'Not connected');
//   }
// });























/**
 * app.js — AgentWatch Dashboard
 *
 * Architecture:
 *   State → API → Render pipeline.
 *   No framework, no bundler.  Vanilla ES2022 modules pattern
 *   (everything in one file for simplicity — split if the project grows).
 *
 * Sections:
 *   1. State management
 *   2. API client (all fetch calls, token injection)
 *   3. Navigation / view router
 *   4. Auth flow (register / paste token)
 *   5. Render: Overview (stats + latency)
 *   6. Render: Violations table
 *   7. Render: Audit log table
 *   8. Utilities (badges, timestamps, toast)
 *   9. Auto-refresh + init
 */

// 'use strict';

// /* ═══════════════════════════════════════════════════════════════
//    1. STATE
// ═══════════════════════════════════════════════════════════════ */

// const state = {
//   token:           null,       // JWT bearer token
//   agentId:         null,       // UUID of the authenticated agent
//   agentName:       null,
//   lastRefresh:     null,
//   refreshTimer:    null,
//   currentView:     'overview',
//   loading: {
//     stats:        false,
//     violations:   false,
//     audit:        false,
//     interactions: false,
//     policies:     false,
//   },
//   data: {
//     stats:        null,
//     violations:   null,    // { violations: [], total, skip, limit }
//     audit:        null,    // { events: [], total, skip, limit }
//     interactions: null,    // { interactions: [], total, skip, limit }
//     policies:     null,    // { policies: [], total, skip, limit }
//   },
//   filters: {
//     auditEventType:    '',
//     violationPage:     0,
//     auditPage:         0,
//     interactionsPage:  0,
//     policiesPage:      0,
//     pageSize:          25,
//   },
// };

// /* ═══════════════════════════════════════════════════════════════
//    2. API CLIENT
// ═══════════════════════════════════════════════════════════════ */

// const API_BASE = '';  // Same origin — served by FastAPI at /dashboard/

// function authHeaders() {
//   return {
//     'Content-Type': 'application/json',
//     ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
//   };
// }

// /**
//  * Generic fetch wrapper.
//  * Returns { ok: true, data } or { ok: false, error, status }.
//  */
// async function apiFetch(path, opts = {}) {
//   try {
//     const res = await fetch(`${API_BASE}${path}`, {
//       ...opts,
//       headers: { ...authHeaders(), ...(opts.headers || {}) },
//     });
//     const data = await res.json().catch(() => null);
//     if (!res.ok) {
//       return { ok: false, error: data?.detail || `HTTP ${res.status}`, status: res.status };
//     }
//     return { ok: true, data };
//   } catch (err) {
//     return { ok: false, error: err.message || 'Network error', status: 0 };
//   }
// }

// async function fetchStats() {
//   return apiFetch('/analytics/stats');
// }

// async function fetchViolations(skip = 0, limit = 25) {
//   return apiFetch(`/governance/violations?skip=${skip}&limit=${limit}`);
// }

// async function fetchInteractions(skip = 0, limit = 25) {
//   return apiFetch(`/agent-interactions?skip=${skip}&limit=${limit}`);
// }

// async function fetchTrust() {
//   return apiFetch('/analytics/trust');
// }

// async function fetchAgentTrust(agentId) {
//   return apiFetch(`/analytics/trust/${agentId}`);
// }

// async function fetchRisk() {
//   return apiFetch('/analytics/risk');
// }

// async function fetchAgentRisk(agentId) {
//   return apiFetch(`/analytics/risk/${agentId}`);
// }

// async function fetchAuditLogs(skip = 0, limit = 25, eventType = '') {
//   const et = eventType ? `&event_type=${encodeURIComponent(eventType)}` : '';
//   return apiFetch(`/audit/logs?skip=${skip}&limit=${limit}${et}`);
// }

// async function fetchPolicies(skip = 0, limit = 25) {
//   return apiFetch(`/policies?skip=${skip}&limit=${limit}`);
// }

// async function registerAgent(name, secret, tools) {
//   return apiFetch('/agents/register', {
//     method: 'POST',
//     body: JSON.stringify({ name, secret, allowed_tools: tools }),
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    3. NAVIGATION
// ═══════════════════════════════════════════════════════════════ */

// function navigateTo(viewId) {
//   state.currentView = viewId;

//   // Update sidebar active state
//   document.querySelectorAll('.nav-item').forEach(el => {
//     el.classList.toggle('active', el.dataset.view === viewId);
//   });

//   // Swap visible view
//   document.querySelectorAll('.view').forEach(el => {
//     el.classList.toggle('active', el.id === `view-${viewId}`);
//   });

//   // Update topbar title
//   const titles = {
//     overview:     { title: 'Overview',              sub: 'Platform health at a glance' },
//     violations:   { title: 'Governance Violations', sub: 'Blocked tool calls and policy enforcement' },
//     audit:        { title: 'Audit Log',             sub: 'Full event trace across all agent runs' },
//     interactions: { title: 'Agent Interactions',    sub: 'Agent-to-agent communication records' },
//     policies:     { title: 'Governance Policies',   sub: 'Named rules enforced before every run' },
//   };
//   const t = titles[viewId] || {};
//   document.getElementById('topbar-title').textContent    = t.title || '';
//   document.getElementById('topbar-subtitle').textContent = t.sub   || '';

//   // Load data for the view
//   if (!state.token) return;
//   if (viewId === 'overview')     refreshOverview();
//   if (viewId === 'violations')   refreshViolations();
//   if (viewId === 'audit')        refreshAuditLog();
//   if (viewId === 'interactions') refreshInteractions();
//   if (viewId === 'policies')     refreshPolicies();
// }

// /* ═══════════════════════════════════════════════════════════════
//    4. AUTH FLOW
// ═══════════════════════════════════════════════════════════════ */

// function setToken(token, agentId, agentName) {
//   state.token     = token;
//   state.agentId   = agentId;
//   state.agentName = agentName || 'unknown';

//   // Persist in sessionStorage (not localStorage — clears on tab close)
//   sessionStorage.setItem('aw_token',      token);
//   sessionStorage.setItem('aw_agent_id',   agentId);
//   sessionStorage.setItem('aw_agent_name', agentName || '');

//   updateConnectionStatus('connected', agentName || agentId?.slice(0, 8) + '…');
//   hideAuthBanner();
//   startAutoRefresh();
//   navigateTo(state.currentView);
// }

// function clearToken() {
//   state.token = state.agentId = state.agentName = null;
//   sessionStorage.removeItem('aw_token');
//   sessionStorage.removeItem('aw_agent_id');
//   sessionStorage.removeItem('aw_agent_name');
//   stopAutoRefresh();
//   updateConnectionStatus('disconnected', 'Not connected');
//   showAuthBanner();
// }

// function tryRestoreSession() {
//   const token     = sessionStorage.getItem('aw_token');
//   const agentId   = sessionStorage.getItem('aw_agent_id');
//   const agentName = sessionStorage.getItem('aw_agent_name');
//   if (token && agentId) {
//     setToken(token, agentId, agentName);
//     return true;
//   }
//   return false;
// }

// function showAuthBanner() {
//   document.getElementById('auth-banner').style.display = 'block';
// }

// function hideAuthBanner() {
//   document.getElementById('auth-banner').style.display = 'none';
// }

// async function handleRegister() {
//   const name   = document.getElementById('reg-name').value.trim();
//   const secret = document.getElementById('reg-secret').value;
//   const tools  = Array.from(
//     document.querySelectorAll('.tool-checkbox:checked')
//   ).map(el => el.value);

//   if (!name || name.length < 2) {
//     return toast('Agent name must be at least 2 characters', 'error');
//   }
//   if (!secret || secret.length < 12) {
//     return toast('Secret must be at least 12 characters', 'error');
//   }

//   const btn = document.getElementById('btn-register');
//   btn.disabled = true;
//   btn.innerHTML = '<span class="btn-icon btn-spinning">⟳</span> Registering…';

//   const { ok, data, error } = await registerAgent(name, secret, tools);

//   btn.disabled = false;
//   btn.innerHTML = '🚀 Register Agent';

//   if (!ok) {
//     toast(error || 'Registration failed', 'error');
//     return;
//   }

//   toast(`Agent "${name}" registered successfully!`, 'success');
//   setToken(data.access_token, data.agent_id, name);
//   document.getElementById('token-display').value = data.access_token;
// }

// async function handlePasteToken() {
//   const raw = document.getElementById('token-display').value.trim();
//   if (!raw) return toast('Paste a JWT token first', 'error');

//   // Decode payload to extract sub (agent_id) and agent_name — no library needed
//   try {
//     const parts = raw.split('.');
//     if (parts.length !== 3) throw new Error('Not a JWT');
//     const payload = JSON.parse(atob(parts[1].replace(/-/g,'+').replace(/_/g,'/')));
//     if (!payload.sub) throw new Error('Token has no sub claim');
//     setToken(raw, payload.sub, payload.agent_name || payload.sub.slice(0, 8) + '…');
//     toast('Token applied successfully', 'success');
//   } catch (e) {
//     toast(`Invalid token: ${e.message}`, 'error');
//   }
// }

// /* ═══════════════════════════════════════════════════════════════
//    5. OVERVIEW — stats cards + latency bars
// ═══════════════════════════════════════════════════════════════ */

// async function refreshOverview() {
//   if (state.loading.stats) return;
//   state.loading.stats = true;

//   // Show skeleton values while loading
//   renderStatSkeletons();

//   const { ok, data, error } = await fetchStats();
//   state.loading.stats = false;

//   if (!ok) {
//     toast(error || 'Failed to load stats', 'error');
//     renderStatsError();
//     return;
//   }

//   state.data.stats = data;
//   renderStats(data);
//   renderLatency(data.tool_latency || []);

//   // Fetch the 5 most recent violations for the overview preview panel.
//   // Separate lightweight call (limit=5) made only when overview is active.
//   // renderViolations on the Violations page is never involved.
//   const vRes = await fetchViolations(0, 5);
//   if (vRes.ok) renderOverviewViolations(vRes.data);
// }

// function renderStatSkeletons() {
//   const ids = ['stat-agents','stat-runs','stat-events','stat-violations','stat-rate','stat-completed','stat-avg-trust','stat-avg-risk'];
//   ids.forEach(id => {
//     const el = document.getElementById(id);
//     if (el) el.innerHTML = '<span class="skeleton" style="display:inline-block;width:48px;height:28px;"></span>';
//   });
// }

// function renderStatsError() {
//   const ids = ['stat-agents','stat-runs','stat-events','stat-violations','stat-rate','stat-completed','stat-avg-trust','stat-avg-risk'];
//   ids.forEach(id => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = '—';
//   });
// }

// function renderStats(s) {
//   const set = (id, val) => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = val;
//   };

//   set('stat-agents',    s.total_agents);
//   set('stat-runs',      s.total_runs);
//   set('stat-events',    s.total_events);
//   set('stat-violations', s.total_violations);
//   set('stat-rate',      `${s.violation_rate.toFixed(1)}%`);
//   set('stat-completed', s.completed_runs);

//   // Sub-lines
//   const setSub = (id, val) => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = val;
//   };
//   setSub('stat-runs-sub',       `${s.completed_runs} completed · ${s.failed_runs} failed`);
//   setSub('stat-violations-sub', s.total_tool_calls > 0
//     ? `out of ${s.total_tool_calls} tool calls`
//     : 'no tool calls yet');

//   // Trust score aggregates
//   if (s.average_trust_score !== undefined) {
//     set('stat-avg-trust', s.average_trust_score.toFixed(1));
//     const dist = s.trust_distribution || {};
//     const distParts = Object.entries(dist)
//       .filter(([,v]) => v > 0)
//       .map(([k, v]) => `${v} ${k}`)
//       .join(' · ');
//     setSub('stat-avg-trust-sub', distParts || 'no agents yet');
//   }

//   // Risk score aggregates
//   if (s.average_risk_score !== undefined) {
//     set('stat-avg-risk', s.average_risk_score.toFixed(1));
//     const rdist = s.risk_distribution || {};
//     const riskParts = Object.entries(rdist)
//       .filter(([,v]) => v > 0)
//       .map(([k, v]) => `${v} ${k}`)
//       .join(' · ');
//     setSub('stat-avg-risk-sub', riskParts || 'no agents yet');
//   }
// }

// function renderLatency(tools) {
//   const grid = document.getElementById('latency-grid');
//   if (!grid) return;

//   if (!tools || tools.length === 0) {
//     grid.innerHTML = `
//       <div class="empty-state" style="padding:24px; grid-column:1/-1;">
//         <span class="empty-icon">⏱</span>
//         <span class="empty-title">No tool executions yet</span>
//         <span class="empty-body">Run an agent with a prompt to see latency data here.</span>
//       </div>`;
//     return;
//   }

//   const maxMs = Math.max(...tools.map(t => t.p95_ms || t.avg_ms || 1), 1);

//   grid.innerHTML = tools.map(t => {
//     const avgPct = Math.min(100, ((t.avg_ms || 0) / maxMs) * 100).toFixed(1);
//     const p95Pct = Math.min(100, ((t.p95_ms || 0) / maxMs) * 100).toFixed(1);

//     return `
//       <div class="latency-card">
//         <div class="latency-header">
//           <span class="latency-tool-name">${escHtml(t.tool_name)}</span>
//           <span class="latency-calls">${t.call_count} calls</span>
//         </div>
//         <div class="latency-bar-row">
//           <span class="latency-bar-label">avg</span>
//           <div class="latency-bar-track">
//             <div class="latency-bar-fill" style="width:${avgPct}%"></div>
//           </div>
//           <span class="latency-val">${t.avg_ms != null ? t.avg_ms.toFixed(1) + 'ms' : '—'}</span>
//         </div>
//         <div class="latency-bar-row">
//           <span class="latency-bar-label">p95</span>
//           <div class="latency-bar-track">
//             <div class="latency-bar-fill p95" style="width:${p95Pct}%"></div>
//           </div>
//           <span class="latency-val">${t.p95_ms != null ? t.p95_ms.toFixed(1) + 'ms' : '—'}</span>
//         </div>
//       </div>`;
//   }).join('');
// }

// /* ═══════════════════════════════════════════════════════════════
//    5b. OVERVIEW VIOLATIONS PREVIEW
//    Renders the 5-row preview panel on the Overview page.
//    Uses data already fetched by refreshOverview() — no extra API call.
//    Completely independent of renderViolations(); neither calls the other.
// ═══════════════════════════════════════════════════════════════ */

// function renderOverviewViolations(data) {
//   const tbody   = document.getElementById('overview-violations-tbody');
//   const counter = document.getElementById('overview-violations-count');
//   if (!tbody) return;

//   if (counter) counter.textContent = data.total;

//   if (!data.violations || data.violations.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="5">
//         <div class="empty-state" style="padding:20px;">
//           <span class="empty-icon">✅</span>
//           <span class="empty-title">No violations yet</span>
//         </div>
//       </td></tr>`;
//     return;
//   }

//   tbody.innerHTML = data.violations.slice(0, 5).map(v => {
//     const inputStr = v.attempted_input
//       ? JSON.stringify(v.attempted_input).slice(0, 50)
//       : '—';
//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(v.timestamp)}</td>
//         <td>${escHtml(v.agent_name || '—')}</td>
//         <td><span class="badge badge-tool">${escHtml(v.tool_name || '—')}</span></td>
//         <td>
//           <span class="violation-input"
//                 style="font-family:var(--font-mono);font-size:11.5px;">
//             ${escHtml(inputStr)}
//           </span>
//         </td>
//         <td style="font-size:12px;color:var(--text-secondary);
//                    max-width:180px;overflow:hidden;
//                    text-overflow:ellipsis;white-space:nowrap;">
//           ${escHtml((v.denial_message || '').slice(0, 60))}
//         </td>
//       </tr>`;
//   }).join('');
// }

// /* ═══════════════════════════════════════════════════════════════
//    6. VIOLATIONS TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshViolations() {
//   if (state.loading.violations) return;
//   state.loading.violations = true;
//   setTableLoading('violations-tbody');

//   const skip  = state.filters.violationPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchViolations(skip, state.filters.pageSize);
//   state.loading.violations = false;

//   if (!ok) {
//     toast(error || 'Failed to load violations', 'error');
//     return;
//   }

//   state.data.violations = data;
//   renderViolations(data);
// }

// function renderViolations(data) {
//   const tbody   = document.getElementById('violations-tbody');
//   const counter = document.getElementById('violations-count');
//   if (counter) counter.textContent = data.total;

//   if (!data.violations || data.violations.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="6">
//         <div class="empty-state">
//           <span class="empty-icon">✅</span>
//           <span class="empty-title">No governance violations</span>
//           <span class="empty-body">All tool calls are within permitted bounds. Register an agent with limited tools and run a prompt to see violations here.</span>
//         </div>
//       </td></tr>`;
//     renderPagination('violations-pagination', data, state.filters.violationPage, (p) => {
//       state.filters.violationPage = p;
//       refreshViolations();
//     });
//     return;
//   }

//   tbody.innerHTML = data.violations.map(v => {
//     const inputStr = v.attempted_input
//       ? JSON.stringify(v.attempted_input)
//       : '—';

//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(v.timestamp)}</td>
//         <td>
//           <span class="badge badge-violation">⛔ violation</span>
//         </td>
//         <td>
//           <span style="font-weight:500;">${escHtml(v.agent_name || '—')}</span>
//           <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(v.agent_id.slice(0,8))}…</span>
//         </td>
//         <td><span class="badge badge-tool">${escHtml(v.tool_name || '—')}</span></td>
//         <td>
//           <div class="violation-detail">
//             <span class="violation-input">${escHtml(inputStr)}</span>
//           </div>
//         </td>
//         <td>
//           <div style="max-width:220px; font-size:12px; color:var(--text-secondary); line-height:1.5;">
//             ${escHtml(v.denial_message || '—')}
//           </div>
//         </td>
//       </tr>`;
//   }).join('');

//   renderPagination('violations-pagination', data, state.filters.violationPage, (p) => {
//     state.filters.violationPage = p;
//     refreshViolations();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    7. AUDIT LOG TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshAuditLog() {
//   if (state.loading.audit) return;
//   state.loading.audit = true;
//   setTableLoading('audit-tbody');

//   const skip = state.filters.auditPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchAuditLogs(
//     skip,
//     state.filters.pageSize,
//     state.filters.auditEventType,
//   );
//   state.loading.audit = false;

//   if (!ok) {
//     toast(error || 'Failed to load audit log', 'error');
//     return;
//   }

//   state.data.audit = data;
//   renderAuditLog(data);
// }

// function renderAuditLog(data) {
//   const tbody   = document.getElementById('audit-tbody');
//   const counter = document.getElementById('audit-count');
//   if (counter) counter.textContent = data.total;

//   if (!data.events || data.events.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="7">
//         <div class="empty-state">
//           <span class="empty-icon">📋</span>
//           <span class="empty-title">No audit events</span>
//           <span class="empty-body">Submit a run via POST /agents/run to populate the audit log.</span>
//         </div>
//       </td></tr>`;
//     renderPagination('audit-pagination', data, state.filters.auditPage, (p) => {
//       state.filters.auditPage = p;
//       refreshAuditLog();
//     });
//     return;
//   }

//   tbody.innerHTML = data.events.map(e => {
//     const inputSummary = e.input_data
//       ? JSON.stringify(e.input_data).slice(0, 60) + (JSON.stringify(e.input_data).length > 60 ? '…' : '')
//       : '—';

//     const outputSummary = e.output_data
//       ? JSON.stringify(e.output_data).slice(0, 60) + (JSON.stringify(e.output_data).length > 60 ? '…' : '')
//       : '—';

//     const permittedBadge = e.permitted === true
//       ? '<span class="badge badge-permitted">✓ allowed</span>'
//       : e.permitted === false
//         ? '<span class="badge badge-blocked">⛔ blocked</span>'
//         : '<span style="color:var(--text-muted);font-size:12px;">—</span>';

//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(e.timestamp)}</td>
//         <td>${eventTypeBadge(e.event_type)}</td>
//         <td>
//           <span style="font-weight:500;">${escHtml(e.agent_name || '—')}</span>
//         </td>
//         <td>
//           ${e.tool_name
//             ? `<span class="badge badge-tool">${escHtml(e.tool_name)}</span>`
//             : '<span class="td-dim">—</span>'
//           }
//         </td>
//         <td>${permittedBadge}</td>
//         <td>
//           <div class="td-truncate td-mono" style="font-size:11.5px; color:var(--text-secondary);">
//             ${escHtml(inputSummary)}
//           </div>
//         </td>
//         <td>
//           ${e.latency_ms != null
//             ? `<span class="td-mono" style="font-size:12px; color:var(--accent-teal);">${e.latency_ms.toFixed(1)}ms</span>`
//             : '<span class="td-dim">—</span>'
//           }
//         </td>
//       </tr>`;
//   }).join('');

//   renderPagination('audit-pagination', data, state.filters.auditPage, (p) => {
//     state.filters.auditPage = p;
//     refreshAuditLog();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    7b. AGENT INTERACTIONS TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshInteractions() {
//   if (state.loading.interactions) return;
//   state.loading.interactions = true;
//   setTableLoading('interactions-tbody');

//   const skip  = state.filters.interactionsPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchInteractions(skip, state.filters.pageSize);
//   state.loading.interactions = false;

//   if (!ok) {
//     toast(error || 'Failed to load interactions', 'error');
//     return;
//   }

//   state.data.interactions = data;
//   renderInteractions(data);
// }

// function renderInteractions(data) {
//   const tbody   = document.getElementById('interactions-tbody');
//   const counter = document.getElementById('interactions-count');
//   if (counter) counter.textContent = data.total;

//   if (!data.interactions || data.interactions.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="5">
//         <div class="empty-state">
//           <span class="empty-icon">🔗</span>
//           <span class="empty-title">No agent interactions yet</span>
//           <span class="empty-body">
//             Use POST /agent-interactions to record a handoff, delegation,
//             request, or response between two registered agents.
//           </span>
//         </div>
//       </td></tr>`;
//     renderPagination('interactions-pagination', data, state.filters.interactionsPage, (p) => {
//       state.filters.interactionsPage = p;
//       refreshInteractions();
//     });
//     return;
//   }

//   const typeBadge = (type) => {
//     const map = {
//       'handoff':    '<span class="badge badge-handoff">⇒ handoff</span>',
//       'delegation': '<span class="badge badge-delegation">↓ delegation</span>',
//       'request':    '<span class="badge badge-request">? request</span>',
//       'response':   '<span class="badge badge-response">✓ response</span>',
//     };
//     return map[type] || `<span class="badge">${escHtml(type)}</span>`;
//   };

//   tbody.innerHTML = data.interactions.map(i => `
//     <tr>
//       <td class="td-mono td-dim">${relativeTime(i.created_at)}</td>
//       <td>${typeBadge(i.interaction_type)}</td>
//       <td>
//         <span style="font-weight:500;">${escHtml(i.source_agent_name || '—')}</span>
//         <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(i.source_agent_id.slice(0,8))}…</span>
//       </td>
//       <td>
//         <span style="font-weight:500;">${escHtml(i.target_agent_name || '—')}</span>
//         <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(i.target_agent_id.slice(0,8))}…</span>
//       </td>
//       <td>
//         <div style="max-width:260px; font-size:12px; color:var(--text-secondary);
//                     overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
//           ${i.message ? escHtml(i.message) : '<span class="td-dim">—</span>'}
//         </div>
//       </td>
//     </tr>`).join('');

//   renderPagination('interactions-pagination', data, state.filters.interactionsPage, (p) => {
//     state.filters.interactionsPage = p;
//     refreshInteractions();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    8. UTILITIES
// ═══════════════════════════════════════════════════════════════ */

// function escHtml(str) {
//   if (str == null) return '';
//   return String(str)
//     .replace(/&/g, '&amp;')
//     .replace(/</g, '&lt;')
//     .replace(/>/g, '&gt;')
//     .replace(/"/g, '&quot;');
// }

// function relativeTime(isoString) {
//   if (!isoString) return '—';
//   const date  = new Date(isoString.endsWith('Z') ? isoString : isoString + 'Z');
//   const diff  = (Date.now() - date.getTime()) / 1000;
//   if (diff < 5)   return 'just now';
//   if (diff < 60)  return `${Math.floor(diff)}s ago`;
//   if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
//   if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
//   return date.toLocaleDateString();
// }

// function eventTypeBadge(type) {
//   const map = {
//     'run_start': '<span class="badge badge-run-start">▶ run_start</span>',
//     'tool_call': '<span class="badge badge-tool-call">⚙ tool_call</span>',
//     'tool_end':  '<span class="badge badge-tool-end">✓ tool_end</span>',
//     'violation': '<span class="badge badge-violation">⛔ violation</span>',
//     'run_end':   '<span class="badge badge-run-end">■ run_end</span>',
//     'agent_handoff':    '<span class="badge badge-handoff">⇒ agent_handoff</span>',
//     'policy_violation': '<span class="badge badge-violation">🛡 policy_violation</span>',
//   };
//   return map[type] || `<span class="badge">${escHtml(type)}</span>`;
// }

// function trustLevelBadge(level) {
//   const map = {
//     'TRUSTED':   '<span class="badge" style="background:rgba(63,185,80,0.15);color:#3fb950;">🏅 TRUSTED</span>',
//     'MONITORED': '<span class="badge" style="background:rgba(79,142,247,0.15);color:#4f8ef7;">👁 MONITORED</span>',
//     'WARNING':   '<span class="badge" style="background:rgba(227,135,62,0.15);color:#e3873e;">⚠ WARNING</span>',
//     'HIGH_RISK': '<span class="badge" style="background:rgba(248,81,73,0.15);color:#f85149;">🔴 HIGH_RISK</span>',
//   };
//   return map[level] || `<span class="badge">${escHtml(level)}</span>`;
// }

// function formatTrustScore(score) {
//   if (score === null || score === undefined) return '—';
//   const color = score >= 90 ? '#3fb950'
//               : score >= 70 ? '#4f8ef7'
//               : score >= 50 ? '#e3873e'
//               : '#f85149';
//   return `<span style="font-family:var(--font-mono);font-weight:700;color:${color};">${score.toFixed(1)}</span>`;
// }

// function riskLevelBadge(level) {
//   const map = {
//     'SAFE':     '<span class="badge" style="background:rgba(63,185,80,0.15);color:#3fb950;">✅ SAFE</span>',
//     'LOW':      '<span class="badge" style="background:rgba(57,197,207,0.15);color:#39c5cf;">🔵 LOW</span>',
//     'MEDIUM':   '<span class="badge" style="background:rgba(227,135,62,0.15);color:#e3873e;">🟡 MEDIUM</span>',
//     'HIGH':     '<span class="badge" style="background:rgba(248,81,73,0.15);color:#f85149;">🔴 HIGH</span>',
//     'CRITICAL': '<span class="badge" style="background:rgba(248,81,73,0.25);color:#f85149;font-weight:700;">🚨 CRITICAL</span>',
//   };
//   return map[level] || `<span class="badge">${escHtml(level)}</span>`;
// }

// function formatRiskScore(score) {
//   if (score === null || score === undefined) return '—';
//   // Risk: high number = danger, so colour is inverted vs trust
//   const color = score >= 75 ? '#f85149'
//               : score >= 50 ? '#e3873e'
//               : score >= 25 ? '#e3c93e'
//               : '#3fb950';
//   return `<span style="font-family:var(--font-mono);font-weight:700;color:${color};">${score.toFixed(1)}</span>`;
// }

// function setTableLoading(tbodyId) {
//   const el = document.getElementById(tbodyId);
//   if (!el) return;
//   el.innerHTML = Array.from({ length: 5 }, () => `
//     <tr>${Array.from({ length: 7 }, () =>
//       `<td><div class="skeleton" style="height:16px; width:${60 + Math.random()*60 | 0}px;"></div></td>`
//     ).join('')}</tr>
//   `).join('');
// }

// function renderPagination(containerId, data, currentPage, onPage) {
//   const el = document.getElementById(containerId);
//   if (!el) return;

//   const totalPages = Math.ceil(data.total / (data.limit || 25));
//   if (totalPages <= 1) { el.innerHTML = ''; return; }

//   const prevDisabled = currentPage === 0;
//   const nextDisabled = currentPage >= totalPages - 1;

//   el.innerHTML = `
//     <div style="display:flex; align-items:center; gap:10px; justify-content:flex-end; padding:12px 14px; border-top:1px solid var(--border);">
//       <span style="font-size:12px; color:var(--text-muted);">
//         ${data.skip + 1}–${Math.min(data.skip + data.limit, data.total)} of ${data.total}
//       </span>
//       <button class="btn btn-ghost btn-sm" ${prevDisabled ? 'disabled' : ''} onclick="(${onPage.toString()})(${currentPage - 1})">
//         ← Prev
//       </button>
//       <button class="btn btn-ghost btn-sm" ${nextDisabled ? 'disabled' : ''} onclick="(${onPage.toString()})(${currentPage + 1})">
//         Next →
//       </button>
//     </div>`;
// }

// function updateConnectionStatus(status, label) {
//   const dot  = document.getElementById('status-dot');
//   const text = document.getElementById('status-text');
//   if (dot)  { dot.className = `status-dot ${status}`; }
//   if (text) { text.textContent = label || status; }
// }

// function updateLastRefresh() {
//   state.lastRefresh = new Date();
//   const el = document.getElementById('last-refresh');
//   if (el) el.textContent = `Refreshed ${relativeTime(state.lastRefresh.toISOString())}`;
// }

// let _refreshLabelTimer = null;
// function startRefreshLabelUpdater() {
//   clearInterval(_refreshLabelTimer);
//   _refreshLabelTimer = setInterval(() => {
//     if (!state.lastRefresh) return;
//     const el = document.getElementById('last-refresh');
//     if (el) el.textContent = `Refreshed ${relativeTime(state.lastRefresh.toISOString())}`;
//   }, 15_000);
// }

// /* Toast notifications */
// function toast(message, type = 'info') {
//   const container = document.getElementById('toast-container');
//   if (!container) return;

//   const icons = { error: '⚠', success: '✓', info: 'ℹ' };
//   const el    = document.createElement('div');
//   el.className = `toast ${type}`;
//   el.innerHTML = `<span>${icons[type] || icons.info}</span><span>${escHtml(message)}</span>`;
//   container.appendChild(el);

//   setTimeout(() => {
//     el.style.animation = 'toast-out 0.2s ease forwards';
//     setTimeout(() => el.remove(), 200);
//   }, 3500);
// }

// /* ═══════════════════════════════════════════════════════════════
//    7c. POLICIES TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshPolicies() {
//   if (state.loading.policies) return;
//   state.loading.policies = true;
//   setTableLoading('policies-tbody');
//   const skip  = state.filters.policiesPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchPolicies(skip, state.filters.pageSize);
//   state.loading.policies = false;
//   if (!ok) { toast(error || 'Failed to load policies', 'error'); return; }
//   state.data.policies = data;
//   renderPolicies(data);
// }

// function renderPolicies(data) {
//   const tbody   = document.getElementById('policies-tbody');
//   const counter = document.getElementById('policies-count');
//   if (!tbody) return;
//   if (counter) counter.textContent = data.total;

//   if (!data.policies || data.policies.length === 0) {
//     tbody.innerHTML = `<tr><td colspan="7"><div class="empty-state">
//       <span class="empty-icon">🛡</span>
//       <span class="empty-title">No policies defined yet</span>
//       <span class="empty-body">Create policies via POST /policies.</span>
//     </div></td></tr>`;
//     renderPagination('policies-pagination', data, state.filters.policiesPage, (p) => {
//       state.filters.policiesPage = p; refreshPolicies();
//     });
//     return;
//   }

//   const severityBadge = (sev) => {
//     const map = {
//       'LOW':      '<span class="badge" style="background:rgba(63,185,80,0.15);color:var(--accent-green);">LOW</span>',
//       'MEDIUM':   '<span class="badge" style="background:rgba(227,135,62,0.15);color:var(--accent-orange);">MEDIUM</span>',
//       'HIGH':     '<span class="badge" style="background:rgba(248,81,73,0.15);color:var(--accent-red);">HIGH</span>',
//       'CRITICAL': '<span class="badge" style="background:rgba(188,140,255,0.15);color:var(--accent-purple);">CRITICAL</span>',
//     };
//     return map[sev] || `<span class="badge">${escHtml(sev)}</span>`;
//   };

//   const ruleTypeBadge = (rt) => {
//     const labels = {
//       'tool_allow':   '✓ tool_allow',
//       'tool_deny':    '⛔ tool_deny',
//       'rate_limit':   '⏱ rate_limit',
//       'prompt_guard': '🔍 prompt_guard',
//       'time_window':  '🕐 time_window',
//     };
//     return `<span class="badge badge-tool">${escHtml(labels[rt] || rt)}</span>`;
//   };

//   tbody.innerHTML = data.policies.map(p => `
//     <tr>
//       <td><span style="font-weight:600;">${escHtml(p.name)}</span>
//         ${p.description ? `<br><span class="td-dim" style="font-size:11px;">${escHtml(p.description.slice(0,50))}</span>` : ''}
//       </td>
//       <td>${ruleTypeBadge(p.rule_type)}</td>
//       <td>${severityBadge(p.severity)}</td>
//       <td>${p.is_active
//           ? '<span class="badge badge-permitted">✓ active</span>'
//           : '<span class="badge badge-run-end">○ inactive</span>'}</td>
//       <td><span class="td-mono" style="font-size:12px;">${p.agent_count}</span></td>
//       <td><div class="td-truncate td-mono" style="font-size:11px;max-width:180px;color:var(--text-secondary);">
//         ${escHtml(JSON.stringify(p.rule_config))}
//       </div></td>
//       <td class="td-mono td-dim">${relativeTime(p.created_at)}</td>
//     </tr>`).join('');

//   renderPagination('policies-pagination', data, state.filters.policiesPage, (p) => {
//     state.filters.policiesPage = p; refreshPolicies();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    9. AUTO-REFRESH + INIT
// ═══════════════════════════════════════════════════════════════ */

// const REFRESH_INTERVAL_MS = 30_000;

// function refreshCurrentView() {
//   if (!state.token) return;

//   const btn = document.getElementById('btn-refresh');
//   if (btn) {
//     btn.classList.add('btn-spinning');
//     btn.querySelector('.btn-icon').style.animation = 'spin 0.8s linear infinite';
//   }

//   const views = {
//     overview:     refreshOverview,
//     violations:   refreshViolations,
//     audit:        refreshAuditLog,
//     interactions: refreshInteractions,
//     policies:     refreshPolicies,
//   };
//   const fn = views[state.currentView];
//   if (fn) fn().finally(() => {
//     updateLastRefresh();
//     if (btn) btn.querySelector('.btn-icon').style.animation = '';
//   });
// }

// function startAutoRefresh() {
//   stopAutoRefresh();
//   state.refreshTimer = setInterval(refreshCurrentView, REFRESH_INTERVAL_MS);
// }

// function stopAutoRefresh() {
//   if (state.refreshTimer) clearInterval(state.refreshTimer);
//   state.refreshTimer = null;
// }

// /* ── Boot ─────────────────────────────────────────────────────── */
// document.addEventListener('DOMContentLoaded', () => {

//   // Nav click handlers
//   document.querySelectorAll('.nav-item[data-view]').forEach(el => {
//     el.addEventListener('click', () => navigateTo(el.dataset.view));
//   });

//   // Refresh button
//   document.getElementById('btn-refresh')?.addEventListener('click', refreshCurrentView);

//   // Disconnect button
//   document.getElementById('btn-disconnect')?.addEventListener('click', () => {
//     clearToken();
//     toast('Disconnected', 'info');
//   });

//   // Register form
//   document.getElementById('btn-register')?.addEventListener('click', handleRegister);

//   // Paste token
//   document.getElementById('btn-use-token')?.addEventListener('click', handlePasteToken);

//   // Audit event type filter
//   document.getElementById('audit-filter-type')?.addEventListener('change', (e) => {
//     state.filters.auditEventType = e.target.value;
//     state.filters.auditPage      = 0;
//     refreshAuditLog();
//   });

//   // Mobile sidebar toggle
//   document.getElementById('mobile-menu-btn')?.addEventListener('click', () => {
//     document.querySelector('.sidebar')?.classList.toggle('open');
//   });

//   // Start relative-time label updater
//   startRefreshLabelUpdater();

//   // Restore session or show auth banner
//   if (!tryRestoreSession()) {
//     showAuthBanner();
//     updateConnectionStatus('disconnected', 'Not connected');
//   }
// });


















/**
 * app.js — AgentWatch Dashboard
 *
 * Architecture:
 *   State → API → Render pipeline.
 *   No framework, no bundler.  Vanilla ES2022 modules pattern
 *   (everything in one file for simplicity — split if the project grows).
 *
 * Sections:
 *   1. State management
 *   2. API client (all fetch calls, token injection)
 *   3. Navigation / view router
 *   4. Auth flow (register / paste token)
 *   5. Render: Overview (stats + latency)
 *   6. Render: Violations table
 *   7. Render: Audit log table
 *   8. Utilities (badges, timestamps, toast)
 *   9. Auto-refresh + init
 */

'use strict';

// /* ═══════════════════════════════════════════════════════════════
//    1. STATE
// ═══════════════════════════════════════════════════════════════ */

// const state = {
//   token:           null,       // JWT bearer token
//   agentId:         null,       // UUID of the authenticated agent
//   agentName:       null,
//   lastRefresh:     null,
//   refreshTimer:    null,
//   currentView:     'overview',
//   loading: {
//     stats:        false,
//     violations:   false,
//     audit:        false,
//     interactions: false,
//     policies:     false,
//   },
//   data: {
//     stats:        null,
//     violations:   null,    // { violations: [], total, skip, limit }
//     audit:        null,    // { events: [], total, skip, limit }
//     interactions: null,    // { interactions: [], total, skip, limit }
//     policies:     null,    // { policies: [], total, skip, limit }
//   },
//   filters: {
//     auditEventType:    '',
//     violationPage:     0,
//     auditPage:         0,
//     interactionsPage:  0,
//     policiesPage:      0,
//     pageSize:          25,
//   },
// };

// /* ═══════════════════════════════════════════════════════════════
//    2. API CLIENT
// ═══════════════════════════════════════════════════════════════ */

// const API_BASE = '';  // Same origin — served by FastAPI at /dashboard/

// function authHeaders() {
//   return {
//     'Content-Type': 'application/json',
//     ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
//   };
// }

// /**
//  * Generic fetch wrapper.
//  * Returns { ok: true, data } or { ok: false, error, status }.
//  */
// async function apiFetch(path, opts = {}) {
//   try {
//     const res = await fetch(`${API_BASE}${path}`, {
//       ...opts,
//       headers: { ...authHeaders(), ...(opts.headers || {}) },
//     });
//     const data = await res.json().catch(() => null);
//     if (!res.ok) {
//       return { ok: false, error: data?.detail || `HTTP ${res.status}`, status: res.status };
//     }
//     return { ok: true, data };
//   } catch (err) {
//     return { ok: false, error: err.message || 'Network error', status: 0 };
//   }
// }

// async function fetchStats() {
//   return apiFetch('/analytics/stats');
// }

// async function fetchViolations(skip = 0, limit = 25) {
//   return apiFetch(`/governance/violations?skip=${skip}&limit=${limit}`);
// }

// async function fetchInteractions(skip = 0, limit = 25) {
//   return apiFetch(`/agent-interactions?skip=${skip}&limit=${limit}`);
// }

// async function fetchTrust() {
//   return apiFetch('/analytics/trust');
// }

// async function fetchAgentTrust(agentId) {
//   return apiFetch(`/analytics/trust/${agentId}`);
// }

// async function fetchRisk() {
//   return apiFetch('/analytics/risk');
// }

// async function fetchAgentRisk(agentId) {
//   return apiFetch(`/analytics/risk/${agentId}`);
// }

// async function fetchAuditLogs(skip = 0, limit = 25, eventType = '') {
//   const et = eventType ? `&event_type=${encodeURIComponent(eventType)}` : '';
//   return apiFetch(`/audit/logs?skip=${skip}&limit=${limit}${et}`);
// }

// async function fetchPolicies(skip = 0, limit = 25) {
//   return apiFetch(`/policies?skip=${skip}&limit=${limit}`);
// }

// async function registerAgent(name, secret, tools) {
//   return apiFetch('/agents/register', {
//     method: 'POST',
//     body: JSON.stringify({ name, secret, allowed_tools: tools }),
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    3. NAVIGATION
// ═══════════════════════════════════════════════════════════════ */

// function navigateTo(viewId) {
//   state.currentView = viewId;

//   // Update sidebar active state
//   document.querySelectorAll('.nav-item').forEach(el => {
//     el.classList.toggle('active', el.dataset.view === viewId);
//   });

//   // Swap visible view
//   document.querySelectorAll('.view').forEach(el => {
//     el.classList.toggle('active', el.id === `view-${viewId}`);
//   });

//   // Update topbar title
//   const titles = {
//     overview:     { title: 'Overview',              sub: 'Platform health at a glance' },
//     violations:   { title: 'Governance Violations', sub: 'Blocked tool calls and policy enforcement' },
//     audit:        { title: 'Audit Log',             sub: 'Full event trace across all agent runs' },
//     interactions: { title: 'Agent Interactions',    sub: 'Agent-to-agent communication records' },
//     policies:     { title: 'Governance Policies',   sub: 'Named rules enforced before every run' },
//   };
//   const t = titles[viewId] || {};
//   document.getElementById('topbar-title').textContent    = t.title || '';
//   document.getElementById('topbar-subtitle').textContent = t.sub   || '';

//   // Load data for the view
//   if (!state.token) return;
//   if (viewId === 'overview')     refreshOverview();
//   if (viewId === 'violations')   refreshViolations();
//   if (viewId === 'audit')        refreshAuditLog();
//   if (viewId === 'interactions') refreshInteractions();
//   if (viewId === 'policies')     refreshPolicies();
// }

// /* ═══════════════════════════════════════════════════════════════
//    4. AUTH FLOW
// ═══════════════════════════════════════════════════════════════ */

// function setToken(token, agentId, agentName) {
//   state.token     = token;
//   state.agentId   = agentId;
//   state.agentName = agentName || 'unknown';

//   // Persist in sessionStorage (not localStorage — clears on tab close)
//   sessionStorage.setItem('aw_token',      token);
//   sessionStorage.setItem('aw_agent_id',   agentId);
//   sessionStorage.setItem('aw_agent_name', agentName || '');

//   updateConnectionStatus('connected', agentName || agentId?.slice(0, 8) + '…');
//   hideAuthBanner();
//   startAutoRefresh();
//   navigateTo(state.currentView);
// }

// function clearToken() {
//   state.token = state.agentId = state.agentName = null;
//   sessionStorage.removeItem('aw_token');
//   sessionStorage.removeItem('aw_agent_id');
//   sessionStorage.removeItem('aw_agent_name');
//   stopAutoRefresh();
//   updateConnectionStatus('disconnected', 'Not connected');
//   showAuthBanner();
// }

// function tryRestoreSession() {
//   const token     = sessionStorage.getItem('aw_token');
//   const agentId   = sessionStorage.getItem('aw_agent_id');
//   const agentName = sessionStorage.getItem('aw_agent_name');
//   if (token && agentId) {
//     setToken(token, agentId, agentName);
//     return true;
//   }
//   return false;
// }

// function showAuthBanner() {
//   document.getElementById('auth-banner').style.display = 'block';
// }

// function hideAuthBanner() {
//   document.getElementById('auth-banner').style.display = 'none';
// }

// async function handleRegister() {
//   const name   = document.getElementById('reg-name').value.trim();
//   const secret = document.getElementById('reg-secret').value;
//   const tools  = Array.from(
//     document.querySelectorAll('.tool-checkbox:checked')
//   ).map(el => el.value);

//   if (!name || name.length < 2) {
//     return toast('Agent name must be at least 2 characters', 'error');
//   }
//   if (!secret || secret.length < 12) {
//     return toast('Secret must be at least 12 characters', 'error');
//   }

//   const btn = document.getElementById('btn-register');
//   btn.disabled = true;
//   btn.innerHTML = '<span class="btn-icon btn-spinning">⟳</span> Registering…';

//   const { ok, data, error } = await registerAgent(name, secret, tools);

//   btn.disabled = false;
//   btn.innerHTML = '🚀 Register Agent';

//   if (!ok) {
//     toast(error || 'Registration failed', 'error');
//     return;
//   }

//   toast(`Agent "${name}" registered successfully!`, 'success');
//   setToken(data.access_token, data.agent_id, name);
//   document.getElementById('token-display').value = data.access_token;
// }

// async function handlePasteToken() {
//   const raw = document.getElementById('token-display').value.trim();
//   if (!raw) return toast('Paste a JWT token first', 'error');

//   // Decode payload to extract sub (agent_id) and agent_name — no library needed
//   try {
//     const parts = raw.split('.');
//     if (parts.length !== 3) throw new Error('Not a JWT');
//     const payload = JSON.parse(atob(parts[1].replace(/-/g,'+').replace(/_/g,'/')));
//     if (!payload.sub) throw new Error('Token has no sub claim');
//     setToken(raw, payload.sub, payload.agent_name || payload.sub.slice(0, 8) + '…');
//     toast('Token applied successfully', 'success');
//   } catch (e) {
//     toast(`Invalid token: ${e.message}`, 'error');
//   }
// }

// /* ═══════════════════════════════════════════════════════════════
//    5. OVERVIEW — stats cards + latency bars
// ═══════════════════════════════════════════════════════════════ */

// async function refreshOverview() {
//   if (state.loading.stats) return;
//   state.loading.stats = true;

//   // Show skeleton values while loading
//   renderStatSkeletons();

//   const { ok, data, error } = await fetchStats();
//   state.loading.stats = false;

//   if (!ok) {
//     toast(error || 'Failed to load stats', 'error');
//     renderStatsError();
//     return;
//   }

//   state.data.stats = data;
//   renderStats(data);
//   renderLatency(data.tool_latency || []);
//   renderAllCharts(data);   // Phase 6 charts

//   // Fetch the 5 most recent violations for the overview preview panel.
//   // Separate lightweight call (limit=5) made only when overview is active.
//   // renderViolations on the Violations page is never involved.
//   const vRes = await fetchViolations(0, 5);
//   if (vRes.ok) renderOverviewViolations(vRes.data);
// }

// function renderStatSkeletons() {
//   const ids = ['stat-agents','stat-runs','stat-events','stat-violations','stat-rate','stat-completed','stat-avg-trust','stat-avg-risk'];
//   ids.forEach(id => {
//     const el = document.getElementById(id);
//     if (el) el.innerHTML = '<span class="skeleton" style="display:inline-block;width:48px;height:28px;"></span>';
//   });
// }

// function renderStatsError() {
//   const ids = ['stat-agents','stat-runs','stat-events','stat-violations','stat-rate','stat-completed','stat-avg-trust','stat-avg-risk'];
//   ids.forEach(id => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = '—';
//   });
// }

// function renderStats(s) {
//   const set = (id, val) => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = val;
//   };

//   set('stat-agents',    s.total_agents);
//   set('stat-runs',      s.total_runs);
//   set('stat-events',    s.total_events);
//   set('stat-violations', s.total_violations);
//   set('stat-rate',      `${s.violation_rate.toFixed(1)}%`);
//   set('stat-completed', s.completed_runs);

//   // Sub-lines
//   const setSub = (id, val) => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = val;
//   };
//   setSub('stat-runs-sub',       `${s.completed_runs} completed · ${s.failed_runs} failed`);
//   setSub('stat-violations-sub', s.total_tool_calls > 0
//     ? `out of ${s.total_tool_calls} tool calls`
//     : 'no tool calls yet');

//   // Trust score aggregates
//   if (s.average_trust_score !== undefined) {
//     set('stat-avg-trust', s.average_trust_score.toFixed(1));
//     const dist = s.trust_distribution || {};
//     const distParts = Object.entries(dist)
//       .filter(([,v]) => v > 0)
//       .map(([k, v]) => `${v} ${k}`)
//       .join(' · ');
//     setSub('stat-avg-trust-sub', distParts || 'no agents yet');
//   }

//   // Risk score aggregates
//   if (s.average_risk_score !== undefined) {
//     set('stat-avg-risk', s.average_risk_score.toFixed(1));
//     const rdist = s.risk_distribution || {};
//     const riskParts = Object.entries(rdist)
//       .filter(([,v]) => v > 0)
//       .map(([k, v]) => `${v} ${k}`)
//       .join(' · ');
//     setSub('stat-avg-risk-sub', riskParts || 'no agents yet');
//   }
// }

// function renderLatency(tools) {
//   const grid = document.getElementById('latency-grid');
//   if (!grid) return;

//   if (!tools || tools.length === 0) {
//     grid.innerHTML = `
//       <div class="empty-state" style="padding:24px; grid-column:1/-1;">
//         <span class="empty-icon">⏱</span>
//         <span class="empty-title">No tool executions yet</span>
//         <span class="empty-body">Run an agent with a prompt to see latency data here.</span>
//       </div>`;
//     return;
//   }

//   const maxMs = Math.max(...tools.map(t => t.p95_ms || t.avg_ms || 1), 1);

//   grid.innerHTML = tools.map(t => {
//     const avgPct = Math.min(100, ((t.avg_ms || 0) / maxMs) * 100).toFixed(1);
//     const p95Pct = Math.min(100, ((t.p95_ms || 0) / maxMs) * 100).toFixed(1);

//     return `
//       <div class="latency-card">
//         <div class="latency-header">
//           <span class="latency-tool-name">${escHtml(t.tool_name)}</span>
//           <span class="latency-calls">${t.call_count} calls</span>
//         </div>
//         <div class="latency-bar-row">
//           <span class="latency-bar-label">avg</span>
//           <div class="latency-bar-track">
//             <div class="latency-bar-fill" style="width:${avgPct}%"></div>
//           </div>
//           <span class="latency-val">${t.avg_ms != null ? t.avg_ms.toFixed(1) + 'ms' : '—'}</span>
//         </div>
//         <div class="latency-bar-row">
//           <span class="latency-bar-label">p95</span>
//           <div class="latency-bar-track">
//             <div class="latency-bar-fill p95" style="width:${p95Pct}%"></div>
//           </div>
//           <span class="latency-val">${t.p95_ms != null ? t.p95_ms.toFixed(1) + 'ms' : '—'}</span>
//         </div>
//       </div>`;
//   }).join('');
// }

// /* ═══════════════════════════════════════════════════════════════
//    5b. OVERVIEW VIOLATIONS PREVIEW
//    Renders the 5-row preview panel on the Overview page.
//    Uses data already fetched by refreshOverview() — no extra API call.
//    Completely independent of renderViolations(); neither calls the other.
// ═══════════════════════════════════════════════════════════════ */

// function renderOverviewViolations(data) {
//   const tbody   = document.getElementById('overview-violations-tbody');
//   const counter = document.getElementById('overview-violations-count');
//   if (!tbody) return;

//   if (counter) counter.textContent = data.total;

//   if (!data.violations || data.violations.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="5">
//         <div class="empty-state" style="padding:20px;">
//           <span class="empty-icon">✅</span>
//           <span class="empty-title">No violations yet</span>
//         </div>
//       </td></tr>`;
//     return;
//   }

//   tbody.innerHTML = data.violations.slice(0, 5).map(v => {
//     const inputStr = v.attempted_input
//       ? JSON.stringify(v.attempted_input).slice(0, 50)
//       : '—';
//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(v.timestamp)}</td>
//         <td>${escHtml(v.agent_name || '—')}</td>
//         <td><span class="badge badge-tool">${escHtml(v.tool_name || '—')}</span></td>
//         <td>
//           <span class="violation-input"
//                 style="font-family:var(--font-mono);font-size:11.5px;">
//             ${escHtml(inputStr)}
//           </span>
//         </td>
//         <td style="font-size:12px;color:var(--text-secondary);
//                    max-width:180px;overflow:hidden;
//                    text-overflow:ellipsis;white-space:nowrap;">
//           ${escHtml((v.denial_message || '').slice(0, 60))}
//         </td>
//       </tr>`;
//   }).join('');
// }

// /* ═══════════════════════════════════════════════════════════════
//    6. VIOLATIONS TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshViolations() {
//   if (state.loading.violations) return;
//   state.loading.violations = true;
//   setTableLoading('violations-tbody');

//   const skip  = state.filters.violationPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchViolations(skip, state.filters.pageSize);
//   state.loading.violations = false;

//   if (!ok) {
//     toast(error || 'Failed to load violations', 'error');
//     return;
//   }

//   state.data.violations = data;
//   renderViolations(data);
// }

// function renderViolations(data) {
//   const tbody   = document.getElementById('violations-tbody');
//   const counter = document.getElementById('violations-count');
//   if (counter) counter.textContent = data.total;

//   if (!data.violations || data.violations.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="6">
//         <div class="empty-state">
//           <span class="empty-icon">✅</span>
//           <span class="empty-title">No governance violations</span>
//           <span class="empty-body">All tool calls are within permitted bounds. Register an agent with limited tools and run a prompt to see violations here.</span>
//         </div>
//       </td></tr>`;
//     renderPagination('violations-pagination', data, state.filters.violationPage, (p) => {
//       state.filters.violationPage = p;
//       refreshViolations();
//     });
//     return;
//   }

//   tbody.innerHTML = data.violations.map(v => {
//     const inputStr = v.attempted_input
//       ? JSON.stringify(v.attempted_input)
//       : '—';

//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(v.timestamp)}</td>
//         <td>
//           <span class="badge badge-violation">⛔ violation</span>
//         </td>
//         <td>
//           <span style="font-weight:500;">${escHtml(v.agent_name || '—')}</span>
//           <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(v.agent_id.slice(0,8))}…</span>
//         </td>
//         <td><span class="badge badge-tool">${escHtml(v.tool_name || '—')}</span></td>
//         <td>
//           <div class="violation-detail">
//             <span class="violation-input">${escHtml(inputStr)}</span>
//           </div>
//         </td>
//         <td>
//           <div style="max-width:220px; font-size:12px; color:var(--text-secondary); line-height:1.5;">
//             ${escHtml(v.denial_message || '—')}
//           </div>
//         </td>
//       </tr>`;
//   }).join('');

//   renderPagination('violations-pagination', data, state.filters.violationPage, (p) => {
//     state.filters.violationPage = p;
//     refreshViolations();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    7. AUDIT LOG TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshAuditLog() {
//   if (state.loading.audit) return;
//   state.loading.audit = true;
//   setTableLoading('audit-tbody');

//   const skip = state.filters.auditPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchAuditLogs(
//     skip,
//     state.filters.pageSize,
//     state.filters.auditEventType,
//   );
//   state.loading.audit = false;

//   if (!ok) {
//     toast(error || 'Failed to load audit log', 'error');
//     return;
//   }

//   state.data.audit = data;
//   renderAuditLog(data);
// }

// function renderAuditLog(data) {
//   const tbody   = document.getElementById('audit-tbody');
//   const counter = document.getElementById('audit-count');
//   if (counter) counter.textContent = data.total;

//   if (!data.events || data.events.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="7">
//         <div class="empty-state">
//           <span class="empty-icon">📋</span>
//           <span class="empty-title">No audit events</span>
//           <span class="empty-body">Submit a run via POST /agents/run to populate the audit log.</span>
//         </div>
//       </td></tr>`;
//     renderPagination('audit-pagination', data, state.filters.auditPage, (p) => {
//       state.filters.auditPage = p;
//       refreshAuditLog();
//     });
//     return;
//   }

//   tbody.innerHTML = data.events.map(e => {
//     const inputSummary = e.input_data
//       ? JSON.stringify(e.input_data).slice(0, 60) + (JSON.stringify(e.input_data).length > 60 ? '…' : '')
//       : '—';

//     const outputSummary = e.output_data
//       ? JSON.stringify(e.output_data).slice(0, 60) + (JSON.stringify(e.output_data).length > 60 ? '…' : '')
//       : '—';

//     const permittedBadge = e.permitted === true
//       ? '<span class="badge badge-permitted">✓ allowed</span>'
//       : e.permitted === false
//         ? '<span class="badge badge-blocked">⛔ blocked</span>'
//         : '<span style="color:var(--text-muted);font-size:12px;">—</span>';

//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(e.timestamp)}</td>
//         <td>${eventTypeBadge(e.event_type)}</td>
//         <td>
//           <span style="font-weight:500;">${escHtml(e.agent_name || '—')}</span>
//         </td>
//         <td>
//           ${e.tool_name
//             ? `<span class="badge badge-tool">${escHtml(e.tool_name)}</span>`
//             : '<span class="td-dim">—</span>'
//           }
//         </td>
//         <td>${permittedBadge}</td>
//         <td>
//           <div class="td-truncate td-mono" style="font-size:11.5px; color:var(--text-secondary);">
//             ${escHtml(inputSummary)}
//           </div>
//         </td>
//         <td>
//           ${e.latency_ms != null
//             ? `<span class="td-mono" style="font-size:12px; color:var(--accent-teal);">${e.latency_ms.toFixed(1)}ms</span>`
//             : '<span class="td-dim">—</span>'
//           }
//         </td>
//       </tr>`;
//   }).join('');

//   renderPagination('audit-pagination', data, state.filters.auditPage, (p) => {
//     state.filters.auditPage = p;
//     refreshAuditLog();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    7b. AGENT INTERACTIONS TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshInteractions() {
//   if (state.loading.interactions) return;
//   state.loading.interactions = true;
//   setTableLoading('interactions-tbody');

//   const skip  = state.filters.interactionsPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchInteractions(skip, state.filters.pageSize);
//   state.loading.interactions = false;

//   if (!ok) {
//     toast(error || 'Failed to load interactions', 'error');
//     return;
//   }

//   state.data.interactions = data;
//   renderInteractions(data);
// }

// function renderInteractions(data) {
//   const tbody   = document.getElementById('interactions-tbody');
//   const counter = document.getElementById('interactions-count');
//   if (counter) counter.textContent = data.total;

//   if (!data.interactions || data.interactions.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="5">
//         <div class="empty-state">
//           <span class="empty-icon">🔗</span>
//           <span class="empty-title">No agent interactions yet</span>
//           <span class="empty-body">
//             Use POST /agent-interactions to record a handoff, delegation,
//             request, or response between two registered agents.
//           </span>
//         </div>
//       </td></tr>`;
//     renderPagination('interactions-pagination', data, state.filters.interactionsPage, (p) => {
//       state.filters.interactionsPage = p;
//       refreshInteractions();
//     });
//     return;
//   }

//   const typeBadge = (type) => {
//     const map = {
//       'handoff':    '<span class="badge badge-handoff">⇒ handoff</span>',
//       'delegation': '<span class="badge badge-delegation">↓ delegation</span>',
//       'request':    '<span class="badge badge-request">? request</span>',
//       'response':   '<span class="badge badge-response">✓ response</span>',
//     };
//     return map[type] || `<span class="badge">${escHtml(type)}</span>`;
//   };

//   tbody.innerHTML = data.interactions.map(i => `
//     <tr>
//       <td class="td-mono td-dim">${relativeTime(i.created_at)}</td>
//       <td>${typeBadge(i.interaction_type)}</td>
//       <td>
//         <span style="font-weight:500;">${escHtml(i.source_agent_name || '—')}</span>
//         <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(i.source_agent_id.slice(0,8))}…</span>
//       </td>
//       <td>
//         <span style="font-weight:500;">${escHtml(i.target_agent_name || '—')}</span>
//         <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(i.target_agent_id.slice(0,8))}…</span>
//       </td>
//       <td>
//         <div style="max-width:260px; font-size:12px; color:var(--text-secondary);
//                     overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
//           ${i.message ? escHtml(i.message) : '<span class="td-dim">—</span>'}
//         </div>
//       </td>
//     </tr>`).join('');

//   renderPagination('interactions-pagination', data, state.filters.interactionsPage, (p) => {
//     state.filters.interactionsPage = p;
//     refreshInteractions();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    8. UTILITIES
// ═══════════════════════════════════════════════════════════════ */

// function escHtml(str) {
//   if (str == null) return '';
//   return String(str)
//     .replace(/&/g, '&amp;')
//     .replace(/</g, '&lt;')
//     .replace(/>/g, '&gt;')
//     .replace(/"/g, '&quot;');
// }

// function relativeTime(isoString) {
//   if (!isoString) return '—';
//   const date  = new Date(isoString.endsWith('Z') ? isoString : isoString + 'Z');
//   const diff  = (Date.now() - date.getTime()) / 1000;
//   if (diff < 5)   return 'just now';
//   if (diff < 60)  return `${Math.floor(diff)}s ago`;
//   if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
//   if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
//   return date.toLocaleDateString();
// }

// function eventTypeBadge(type) {
//   const map = {
//     'run_start': '<span class="badge badge-run-start">▶ run_start</span>',
//     'tool_call': '<span class="badge badge-tool-call">⚙ tool_call</span>',
//     'tool_end':  '<span class="badge badge-tool-end">✓ tool_end</span>',
//     'violation': '<span class="badge badge-violation">⛔ violation</span>',
//     'run_end':   '<span class="badge badge-run-end">■ run_end</span>',
//     'agent_handoff':    '<span class="badge badge-handoff">⇒ agent_handoff</span>',
//     'policy_violation': '<span class="badge badge-violation">🛡 policy_violation</span>',
//   };
//   return map[type] || `<span class="badge">${escHtml(type)}</span>`;
// }

// function trustLevelBadge(level) {
//   const map = {
//     'TRUSTED':   '<span class="badge" style="background:rgba(63,185,80,0.15);color:#3fb950;">🏅 TRUSTED</span>',
//     'MONITORED': '<span class="badge" style="background:rgba(79,142,247,0.15);color:#4f8ef7;">👁 MONITORED</span>',
//     'WARNING':   '<span class="badge" style="background:rgba(227,135,62,0.15);color:#e3873e;">⚠ WARNING</span>',
//     'HIGH_RISK': '<span class="badge" style="background:rgba(248,81,73,0.15);color:#f85149;">🔴 HIGH_RISK</span>',
//   };
//   return map[level] || `<span class="badge">${escHtml(level)}</span>`;
// }

// function formatTrustScore(score) {
//   if (score === null || score === undefined) return '—';
//   const color = score >= 90 ? '#3fb950'
//               : score >= 70 ? '#4f8ef7'
//               : score >= 50 ? '#e3873e'
//               : '#f85149';
//   return `<span style="font-family:var(--font-mono);font-weight:700;color:${color};">${score.toFixed(1)}</span>`;
// }

// function riskLevelBadge(level) {
//   const map = {
//     'SAFE':     '<span class="badge" style="background:rgba(63,185,80,0.15);color:#3fb950;">✅ SAFE</span>',
//     'LOW':      '<span class="badge" style="background:rgba(57,197,207,0.15);color:#39c5cf;">🔵 LOW</span>',
//     'MEDIUM':   '<span class="badge" style="background:rgba(227,135,62,0.15);color:#e3873e;">🟡 MEDIUM</span>',
//     'HIGH':     '<span class="badge" style="background:rgba(248,81,73,0.15);color:#f85149;">🔴 HIGH</span>',
//     'CRITICAL': '<span class="badge" style="background:rgba(248,81,73,0.25);color:#f85149;font-weight:700;">🚨 CRITICAL</span>',
//   };
//   return map[level] || `<span class="badge">${escHtml(level)}</span>`;
// }

// function formatRiskScore(score) {
//   if (score === null || score === undefined) return '—';
//   // Risk: high number = danger, so colour is inverted vs trust
//   const color = score >= 75 ? '#f85149'
//               : score >= 50 ? '#e3873e'
//               : score >= 25 ? '#e3c93e'
//               : '#3fb950';
//   return `<span style="font-family:var(--font-mono);font-weight:700;color:${color};">${score.toFixed(1)}</span>`;
// }

// function setTableLoading(tbodyId) {
//   const el = document.getElementById(tbodyId);
//   if (!el) return;
//   el.innerHTML = Array.from({ length: 5 }, () => `
//     <tr>${Array.from({ length: 7 }, () =>
//       `<td><div class="skeleton" style="height:16px; width:${60 + Math.random()*60 | 0}px;"></div></td>`
//     ).join('')}</tr>
//   `).join('');
// }

// function renderPagination(containerId, data, currentPage, onPage) {
//   const el = document.getElementById(containerId);
//   if (!el) return;

//   const totalPages = Math.ceil(data.total / (data.limit || 25));
//   if (totalPages <= 1) { el.innerHTML = ''; return; }

//   const prevDisabled = currentPage === 0;
//   const nextDisabled = currentPage >= totalPages - 1;

//   el.innerHTML = `
//     <div style="display:flex; align-items:center; gap:10px; justify-content:flex-end; padding:12px 14px; border-top:1px solid var(--border);">
//       <span style="font-size:12px; color:var(--text-muted);">
//         ${data.skip + 1}–${Math.min(data.skip + data.limit, data.total)} of ${data.total}
//       </span>
//       <button class="btn btn-ghost btn-sm" ${prevDisabled ? 'disabled' : ''} onclick="(${onPage.toString()})(${currentPage - 1})">
//         ← Prev
//       </button>
//       <button class="btn btn-ghost btn-sm" ${nextDisabled ? 'disabled' : ''} onclick="(${onPage.toString()})(${currentPage + 1})">
//         Next →
//       </button>
//     </div>`;
// }

// function updateConnectionStatus(status, label) {
//   const dot  = document.getElementById('status-dot');
//   const text = document.getElementById('status-text');
//   if (dot)  { dot.className = `status-dot ${status}`; }
//   if (text) { text.textContent = label || status; }
// }

// function updateLastRefresh() {
//   state.lastRefresh = new Date();
//   const el = document.getElementById('last-refresh');
//   if (el) el.textContent = `Refreshed ${relativeTime(state.lastRefresh.toISOString())}`;
// }

// let _refreshLabelTimer = null;
// function startRefreshLabelUpdater() {
//   clearInterval(_refreshLabelTimer);
//   _refreshLabelTimer = setInterval(() => {
//     if (!state.lastRefresh) return;
//     const el = document.getElementById('last-refresh');
//     if (el) el.textContent = `Refreshed ${relativeTime(state.lastRefresh.toISOString())}`;
//   }, 15_000);
// }

// /* Toast notifications */
// function toast(message, type = 'info') {
//   const container = document.getElementById('toast-container');
//   if (!container) return;

//   const icons = { error: '⚠', success: '✓', info: 'ℹ' };
//   const el    = document.createElement('div');
//   el.className = `toast ${type}`;
//   el.innerHTML = `<span>${icons[type] || icons.info}</span><span>${escHtml(message)}</span>`;
//   container.appendChild(el);

//   setTimeout(() => {
//     el.style.animation = 'toast-out 0.2s ease forwards';
//     setTimeout(() => el.remove(), 200);
//   }, 3500);
// }

// /* ═══════════════════════════════════════════════════════════════
//    7c. POLICIES TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshPolicies() {
//   if (state.loading.policies) return;
//   state.loading.policies = true;
//   setTableLoading('policies-tbody');
//   const skip  = state.filters.policiesPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchPolicies(skip, state.filters.pageSize);
//   state.loading.policies = false;
//   if (!ok) { toast(error || 'Failed to load policies', 'error'); return; }
//   state.data.policies = data;
//   renderPolicies(data);
// }

// function renderPolicies(data) {
//   const tbody   = document.getElementById('policies-tbody');
//   const counter = document.getElementById('policies-count');
//   if (!tbody) return;
//   if (counter) counter.textContent = data.total;

//   if (!data.policies || data.policies.length === 0) {
//     tbody.innerHTML = `<tr><td colspan="7"><div class="empty-state">
//       <span class="empty-icon">🛡</span>
//       <span class="empty-title">No policies defined yet</span>
//       <span class="empty-body">Create policies via POST /policies.</span>
//     </div></td></tr>`;
//     renderPagination('policies-pagination', data, state.filters.policiesPage, (p) => {
//       state.filters.policiesPage = p; refreshPolicies();
//     });
//     return;
//   }

//   const severityBadge = (sev) => {
//     const map = {
//       'LOW':      '<span class="badge" style="background:rgba(63,185,80,0.15);color:var(--accent-green);">LOW</span>',
//       'MEDIUM':   '<span class="badge" style="background:rgba(227,135,62,0.15);color:var(--accent-orange);">MEDIUM</span>',
//       'HIGH':     '<span class="badge" style="background:rgba(248,81,73,0.15);color:var(--accent-red);">HIGH</span>',
//       'CRITICAL': '<span class="badge" style="background:rgba(188,140,255,0.15);color:var(--accent-purple);">CRITICAL</span>',
//     };
//     return map[sev] || `<span class="badge">${escHtml(sev)}</span>`;
//   };

//   const ruleTypeBadge = (rt) => {
//     const labels = {
//       'tool_allow':   '✓ tool_allow',
//       'tool_deny':    '⛔ tool_deny',
//       'rate_limit':   '⏱ rate_limit',
//       'prompt_guard': '🔍 prompt_guard',
//       'time_window':  '🕐 time_window',
//     };
//     return `<span class="badge badge-tool">${escHtml(labels[rt] || rt)}</span>`;
//   };

//   tbody.innerHTML = data.policies.map(p => `
//     <tr>
//       <td><span style="font-weight:600;">${escHtml(p.name)}</span>
//         ${p.description ? `<br><span class="td-dim" style="font-size:11px;">${escHtml(p.description.slice(0,50))}</span>` : ''}
//       </td>
//       <td>${ruleTypeBadge(p.rule_type)}</td>
//       <td>${severityBadge(p.severity)}</td>
//       <td>${p.is_active
//           ? '<span class="badge badge-permitted">✓ active</span>'
//           : '<span class="badge badge-run-end">○ inactive</span>'}</td>
//       <td><span class="td-mono" style="font-size:12px;">${p.agent_count}</span></td>
//       <td><div class="td-truncate td-mono" style="font-size:11px;max-width:180px;color:var(--text-secondary);">
//         ${escHtml(JSON.stringify(p.rule_config))}
//       </div></td>
//       <td class="td-mono td-dim">${relativeTime(p.created_at)}</td>
//     </tr>`).join('');

//   renderPagination('policies-pagination', data, state.filters.policiesPage, (p) => {
//     state.filters.policiesPage = p; refreshPolicies();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    8b. CHARTS (Chart.js — Phase 6)
//    All chart data comes from GET /analytics/stats which is already
//    called by refreshOverview(). Charts re-render on every refresh.
//    Chart instances are stored in _charts{} so we can destroy them
//    before re-creating — Chart.js requires this to avoid canvas leaks.
// ═══════════════════════════════════════════════════════════════ */

// // Registry of active Chart.js instances, keyed by canvas id.
// const _charts = {};

// function _destroyChart(id) {
//   if (_charts[id]) {
//     _charts[id].destroy();
//     delete _charts[id];
//   }
// }

// const _CHART_DEFAULTS = {
//   font: { family: 'Inter, system-ui, sans-serif', size: 12 },
//   color: '#8b9ab4',
// };

// const _TRUST_COLORS = ['#3fb950','#4f8ef7','#e3873e','#f85149'];
// const _RISK_COLORS  = ['#3fb950','#39c5cf','#e3c93e','#f85149','#b80000'];
// const _INTER_COLORS = ['#4f8ef7','#bc8cff','#e3873e','#39c5cf'];
// const _TOOL_COLORS  = ['#4f8ef7','#39c5cf','#3fb950','#bc8cff',
//                        '#e3873e','#f85149','#e3c93e','#8b9ab4',
//                        '#ff7eb3','#43c4e8'];

// function _showChartEmpty(id, show) {
//   const empty  = document.getElementById('chart-' + id + '-empty');
//   const canvas = document.getElementById('chart-' + id);
//   if (!empty || !canvas) return;
//   if (show) {
//     empty.style.display  = 'flex';
//     canvas.style.display = 'none';
//   } else {
//     empty.style.display  = 'none';
//     canvas.style.display = 'block';
//   }
// }

// function renderTrustChart(trustDist) {
//   const id = 'trust';
//   _destroyChart(id);
//   const labels = ['TRUSTED','MONITORED','WARNING','HIGH_RISK'];
//   const values = labels.map(l => (trustDist || {})[l] || 0);
//   if (values.every(v => v === 0)) { _showChartEmpty(id, true); return; }
//   _showChartEmpty(id, false);
//   const ctx = document.getElementById('chart-' + id).getContext('2d');
//   _charts[id] = new Chart(ctx, {
//     type: 'doughnut',
//     data: {
//       labels,
//       datasets: [{ data: values, backgroundColor: _TRUST_COLORS,
//                    borderColor: '#1c2333', borderWidth: 2, hoverOffset: 6 }],
//     },
//     options: {
//       responsive: true, maintainAspectRatio: false,
//       plugins: {
//         legend: { position: 'right',
//                   labels: { color: '#8b9ab4', font: _CHART_DEFAULTS.font, boxWidth: 14 } },
//         tooltip: { callbacks: { label: c => ' ' + c.label + ': ' + c.raw + (c.raw !== 1 ? ' agents' : ' agent') } },
//       },
//     },
//   });
// }

// function renderRiskChart(riskDist) {
//   const id = 'risk';
//   _destroyChart(id);
//   const labels = ['SAFE','LOW','MEDIUM','HIGH','CRITICAL'];
//   const values = labels.map(l => (riskDist || {})[l] || 0);
//   if (values.every(v => v === 0)) { _showChartEmpty(id, true); return; }
//   _showChartEmpty(id, false);
//   const ctx = document.getElementById('chart-' + id).getContext('2d');
//   _charts[id] = new Chart(ctx, {
//     type: 'doughnut',
//     data: {
//       labels,
//       datasets: [{ data: values, backgroundColor: _RISK_COLORS,
//                    borderColor: '#1c2333', borderWidth: 2, hoverOffset: 6 }],
//     },
//     options: {
//       responsive: true, maintainAspectRatio: false,
//       plugins: {
//         legend: { position: 'right',
//                   labels: { color: '#8b9ab4', font: _CHART_DEFAULTS.font, boxWidth: 14 } },
//         tooltip: { callbacks: { label: c => ' ' + c.label + ': ' + c.raw + (c.raw !== 1 ? ' agents' : ' agent') } },
//       },
//     },
//   });
// }

// function renderInteractionChart(interactionsByType) {
//   const id = 'interactions';
//   _destroyChart(id);
//   const entries = Object.entries(interactionsByType || {}).filter(function(e) { return e[1] > 0; });
//   if (entries.length === 0) { _showChartEmpty(id, true); return; }
//   _showChartEmpty(id, false);
//   const labels = entries.map(function(e) { return e[0]; });
//   const values = entries.map(function(e) { return e[1]; });
//   const ctx = document.getElementById('chart-' + id).getContext('2d');
//   _charts[id] = new Chart(ctx, {
//     type: 'pie',
//     data: {
//       labels,
//       datasets: [{ data: values,
//                    backgroundColor: _INTER_COLORS.slice(0, labels.length),
//                    borderColor: '#1c2333', borderWidth: 2, hoverOffset: 6 }],
//     },
//     options: {
//       responsive: true, maintainAspectRatio: false,
//       plugins: {
//         legend: { position: 'right',
//                   labels: { color: '#8b9ab4', font: _CHART_DEFAULTS.font, boxWidth: 14 } },
//         tooltip: { callbacks: { label: c => ' ' + c.label + ': ' + c.raw } },
//       },
//     },
//   });
// }

// function renderToolUsageChart(toolLatency) {
//   const id = 'tools';
//   _destroyChart(id);
//   const data = (toolLatency || []).filter(function(t) { return t.call_count > 0; });
//   if (data.length === 0) { _showChartEmpty(id, true); return; }
//   _showChartEmpty(id, false);
//   const labels = data.map(function(t) { return t.tool_name; });
//   const values = data.map(function(t) { return t.call_count; });
//   const ctx = document.getElementById('chart-' + id).getContext('2d');
//   _charts[id] = new Chart(ctx, {
//     type: 'bar',
//     data: {
//       labels,
//       datasets: [{ label: 'Tool Calls', data: values,
//                    backgroundColor: _TOOL_COLORS.slice(0, labels.length),
//                    borderColor: '#1c2333', borderWidth: 1, borderRadius: 4 }],
//     },
//     options: {
//       responsive: true, maintainAspectRatio: false,
//       plugins: {
//         legend: { display: false },
//         tooltip: { callbacks: { label: c => ' ' + c.raw + (c.raw !== 1 ? ' calls' : ' call') } },
//       },
//       scales: {
//         x: { ticks: { color: '#8b9ab4', font: _CHART_DEFAULTS.font },
//              grid:  { color: 'rgba(42,52,80,0.5)' } },
//         y: { beginAtZero: true,
//              ticks: { color: '#8b9ab4', font: _CHART_DEFAULTS.font, precision: 0 },
//              grid:  { color: 'rgba(42,52,80,0.5)' } },
//       },
//     },
//   });
// }

// function renderViolationChart(totalViolations, totalPolicyViolations) {
//   const id = 'violations';
//   _destroyChart(id);
//   const gov = totalViolations || 0;
//   const pol = totalPolicyViolations || 0;
//   if (gov === 0 && pol === 0) { _showChartEmpty(id, true); return; }
//   _showChartEmpty(id, false);
//   const ctx = document.getElementById('chart-' + id).getContext('2d');
//   _charts[id] = new Chart(ctx, {
//     type: 'bar',
//     data: {
//       labels: ['Governance Violations', 'Policy Violations'],
//       datasets: [{ label: 'Count', data: [gov, pol],
//                    backgroundColor: ['#f85149','#e3873e'],
//                    borderColor: '#1c2333', borderWidth: 1,
//                    borderRadius: 4, barThickness: 48 }],
//     },
//     options: {
//       responsive: true, maintainAspectRatio: false,
//       plugins: {
//         legend: { display: false },
//         tooltip: { callbacks: { label: c => ' ' + c.raw + (c.raw !== 1 ? ' violations' : ' violation') } },
//       },
//       scales: {
//         x: { ticks: { color: '#8b9ab4', font: _CHART_DEFAULTS.font },
//              grid:  { color: 'rgba(42,52,80,0.5)' } },
//         y: { beginAtZero: true,
//              ticks: { color: '#8b9ab4', font: _CHART_DEFAULTS.font, precision: 0 },
//              grid:  { color: 'rgba(42,52,80,0.5)' } },
//       },
//     },
//   });
// }

// function renderAllCharts(statsData) {
//   renderTrustChart(statsData.trust_distribution || {});
//   renderRiskChart(statsData.risk_distribution || {});
//   renderInteractionChart(statsData.interactions_by_type || {});
//   renderToolUsageChart(statsData.tool_latency || []);
//   renderViolationChart(
//     statsData.total_violations || 0,
//     statsData.total_policy_violations || 0
//   );
// }

// /* ═══════════════════════════════════════════════════════════════
//    9. AUTO-REFRESH + INIT
// ═══════════════════════════════════════════════════════════════ */

// const REFRESH_INTERVAL_MS = 30_000;

// function refreshCurrentView() {
//   if (!state.token) return;

//   const btn = document.getElementById('btn-refresh');
//   if (btn) {
//     btn.classList.add('btn-spinning');
//     btn.querySelector('.btn-icon').style.animation = 'spin 0.8s linear infinite';
//   }

//   const views = {
//     overview:     refreshOverview,
//     violations:   refreshViolations,
//     audit:        refreshAuditLog,
//     interactions: refreshInteractions,
//     policies:     refreshPolicies,
//   };
//   const fn = views[state.currentView];
//   if (fn) fn().finally(() => {
//     updateLastRefresh();
//     if (btn) btn.querySelector('.btn-icon').style.animation = '';
//   });
// }

// function startAutoRefresh() {
//   stopAutoRefresh();
//   state.refreshTimer = setInterval(refreshCurrentView, REFRESH_INTERVAL_MS);
// }

// function stopAutoRefresh() {
//   if (state.refreshTimer) clearInterval(state.refreshTimer);
//   state.refreshTimer = null;
// }

// /* ── Boot ─────────────────────────────────────────────────────── */
// document.addEventListener('DOMContentLoaded', () => {

//   // Nav click handlers
//   document.querySelectorAll('.nav-item[data-view]').forEach(el => {
//     el.addEventListener('click', () => navigateTo(el.dataset.view));
//   });

//   // Refresh button
//   document.getElementById('btn-refresh')?.addEventListener('click', refreshCurrentView);

//   // Disconnect button
//   document.getElementById('btn-disconnect')?.addEventListener('click', () => {
//     clearToken();
//     toast('Disconnected', 'info');
//   });

//   // Register form
//   document.getElementById('btn-register')?.addEventListener('click', handleRegister);

//   // Paste token
//   document.getElementById('btn-use-token')?.addEventListener('click', handlePasteToken);

//   // Audit event type filter
//   document.getElementById('audit-filter-type')?.addEventListener('change', (e) => {
//     state.filters.auditEventType = e.target.value;
//     state.filters.auditPage      = 0;
//     refreshAuditLog();
//   });

//   // Mobile sidebar toggle
//   document.getElementById('mobile-menu-btn')?.addEventListener('click', () => {
//     document.querySelector('.sidebar')?.classList.toggle('open');
//   });

//   // Start relative-time label updater
//   startRefreshLabelUpdater();

//   // Restore session or show auth banner
//   if (!tryRestoreSession()) {
//     showAuthBanner();
//     updateConnectionStatus('disconnected', 'Not connected');
//   }
// });





/**
 * app.js — AgentWatch Dashboard
 *
 * Architecture:
 *   State → API → Render pipeline.
 *   No framework, no bundler.  Vanilla ES2022 modules pattern
 *   (everything in one file for simplicity — split if the project grows).
 *
 * Sections:
 *   1. State management
 *   2. API client (all fetch calls, token injection)
 *   3. Navigation / view router
 *   4. Auth flow (register / paste token)
 *   5. Render: Overview (stats + latency)
 *   6. Render: Violations table
 *   7. Render: Audit log table
 *   8. Utilities (badges, timestamps, toast)
 *   9. Auto-refresh + init
 */

// 'use strict';

// /* ═══════════════════════════════════════════════════════════════
//    1. STATE
// ═══════════════════════════════════════════════════════════════ */

// const state = {
//   token:           null,       // JWT bearer token
//   agentId:         null,       // UUID of the authenticated agent
//   agentName:       null,
//   lastRefresh:     null,
//   refreshTimer:    null,
//   currentView:     'overview',
//   loading: {
//     stats:        false,
//     violations:   false,
//     audit:        false,
//     interactions: false,
//     policies:     false,
//   },
//   data: {
//     stats:        null,
//     violations:   null,    // { violations: [], total, skip, limit }
//     audit:        null,    // { events: [], total, skip, limit }
//     interactions: null,    // { interactions: [], total, skip, limit }
//     policies:     null,    // { policies: [], total, skip, limit }
//   },
//   filters: {
//     auditEventType:    '',
//     violationPage:     0,
//     auditPage:         0,
//     interactionsPage:  0,
//     policiesPage:      0,
//     pageSize:          25,
//   },
// };

// /* ═══════════════════════════════════════════════════════════════
//    2. API CLIENT
// ═══════════════════════════════════════════════════════════════ */

// const API_BASE = '';  // Same origin — served by FastAPI at /dashboard/

// function authHeaders() {
//   return {
//     'Content-Type': 'application/json',
//     ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
//   };
// }

// /**
//  * Generic fetch wrapper.
//  * Returns { ok: true, data } or { ok: false, error, status }.
//  */
// async function apiFetch(path, opts = {}) {
//   try {
//     const res = await fetch(`${API_BASE}${path}`, {
//       ...opts,
//       headers: { ...authHeaders(), ...(opts.headers || {}) },
//     });
//     const data = await res.json().catch(() => null);
//     if (!res.ok) {
//       return { ok: false, error: data?.detail || `HTTP ${res.status}`, status: res.status };
//     }
//     return { ok: true, data };
//   } catch (err) {
//     return { ok: false, error: err.message || 'Network error', status: 0 };
//   }
// }

// async function fetchStats() {
//   return apiFetch('/analytics/stats');
// }

// async function fetchViolations(skip = 0, limit = 25) {
//   return apiFetch(`/governance/violations?skip=${skip}&limit=${limit}`);
// }

// async function fetchInteractions(skip = 0, limit = 25) {
//   return apiFetch(`/agent-interactions?skip=${skip}&limit=${limit}`);
// }

// async function fetchTrust() {
//   return apiFetch('/analytics/trust');
// }

// async function fetchAgentTrust(agentId) {
//   return apiFetch(`/analytics/trust/${agentId}`);
// }

// async function fetchRisk() {
//   return apiFetch('/analytics/risk');
// }

// async function fetchAgentRisk(agentId) {
//   return apiFetch(`/analytics/risk/${agentId}`);
// }

// async function fetchAuditLogs(skip = 0, limit = 25, eventType = '') {
//   const et = eventType ? `&event_type=${encodeURIComponent(eventType)}` : '';
//   return apiFetch(`/audit/logs?skip=${skip}&limit=${limit}${et}`);
// }

// async function fetchPolicies(skip = 0, limit = 25) {
//   return apiFetch(`/policies?skip=${skip}&limit=${limit}`);
// }

// async function registerAgent(name, secret, tools) {
//   return apiFetch('/agents/register', {
//     method: 'POST',
//     body: JSON.stringify({ name, secret, allowed_tools: tools }),
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    3. NAVIGATION
// ═══════════════════════════════════════════════════════════════ */

// function navigateTo(viewId) {
//   state.currentView = viewId;

//   // Update sidebar active state
//   document.querySelectorAll('.nav-item').forEach(el => {
//     el.classList.toggle('active', el.dataset.view === viewId);
//   });

//   // Swap visible view
//   document.querySelectorAll('.view').forEach(el => {
//     el.classList.toggle('active', el.id === `view-${viewId}`);
//   });

//   // Update topbar title
//   const titles = {
//     overview:     { title: 'Overview',              sub: 'Platform health at a glance' },
//     violations:   { title: 'Governance Violations', sub: 'Blocked tool calls and policy enforcement' },
//     audit:        { title: 'Audit Log',             sub: 'Full event trace across all agent runs' },
//     interactions: { title: 'Agent Interactions',    sub: 'Agent-to-agent communication records' },
//     policies:     { title: 'Governance Policies',   sub: 'Named rules enforced before every run' },
//   };
//   const t = titles[viewId] || {};
//   document.getElementById('topbar-title').textContent    = t.title || '';
//   document.getElementById('topbar-subtitle').textContent = t.sub   || '';

//   // Load data for the view
//   if (!state.token) return;
//   if (viewId === 'overview')     refreshOverview();
//   if (viewId === 'violations')   refreshViolations();
//   if (viewId === 'audit')        refreshAuditLog();
//   if (viewId === 'interactions') refreshInteractions();
//   if (viewId === 'policies')     refreshPolicies();
// }

// /* ═══════════════════════════════════════════════════════════════
//    4. AUTH FLOW
// ═══════════════════════════════════════════════════════════════ */

// function setToken(token, agentId, agentName) {
//   state.token     = token;
//   state.agentId   = agentId;
//   state.agentName = agentName || 'unknown';

//   // Persist in sessionStorage (not localStorage — clears on tab close)
//   sessionStorage.setItem('aw_token',      token);
//   sessionStorage.setItem('aw_agent_id',   agentId);
//   sessionStorage.setItem('aw_agent_name', agentName || '');

//   updateConnectionStatus('connected', agentName || agentId?.slice(0, 8) + '…');
//   hideAuthBanner();
//   startAutoRefresh();
//   navigateTo(state.currentView);
// }

// function clearToken() {
//   state.token = state.agentId = state.agentName = null;
//   sessionStorage.removeItem('aw_token');
//   sessionStorage.removeItem('aw_agent_id');
//   sessionStorage.removeItem('aw_agent_name');
//   stopAutoRefresh();
//   updateConnectionStatus('disconnected', 'Not connected');
//   showAuthBanner();
// }

// function tryRestoreSession() {
//   const token     = sessionStorage.getItem('aw_token');
//   const agentId   = sessionStorage.getItem('aw_agent_id');
//   const agentName = sessionStorage.getItem('aw_agent_name');
//   if (token && agentId) {
//     setToken(token, agentId, agentName);
//     return true;
//   }
//   return false;
// }

// function showAuthBanner() {
//   document.getElementById('auth-banner').style.display = 'block';
// }

// function hideAuthBanner() {
//   document.getElementById('auth-banner').style.display = 'none';
// }

// async function handleRegister() {
//   const name   = document.getElementById('reg-name').value.trim();
//   const secret = document.getElementById('reg-secret').value;
//   const tools  = Array.from(
//     document.querySelectorAll('.tool-checkbox:checked')
//   ).map(el => el.value);

//   if (!name || name.length < 2) {
//     return toast('Agent name must be at least 2 characters', 'error');
//   }
//   if (!secret || secret.length < 12) {
//     return toast('Secret must be at least 12 characters', 'error');
//   }

//   const btn = document.getElementById('btn-register');
//   btn.disabled = true;
//   btn.innerHTML = '<span class="btn-icon btn-spinning">⟳</span> Registering…';

//   const { ok, data, error } = await registerAgent(name, secret, tools);

//   btn.disabled = false;
//   btn.innerHTML = '🚀 Register Agent';

//   if (!ok) {
//     toast(error || 'Registration failed', 'error');
//     return;
//   }

//   toast(`Agent "${name}" registered successfully!`, 'success');
//   setToken(data.access_token, data.agent_id, name);
//   document.getElementById('token-display').value = data.access_token;
// }

// async function handlePasteToken() {
//   const raw = document.getElementById('token-display').value.trim();
//   if (!raw) return toast('Paste a JWT token first', 'error');

//   // Decode payload to extract sub (agent_id) and agent_name — no library needed
//   try {
//     const parts = raw.split('.');
//     if (parts.length !== 3) throw new Error('Not a JWT');
//     const payload = JSON.parse(atob(parts[1].replace(/-/g,'+').replace(/_/g,'/')));
//     if (!payload.sub) throw new Error('Token has no sub claim');
//     setToken(raw, payload.sub, payload.agent_name || payload.sub.slice(0, 8) + '…');
//     toast('Token applied successfully', 'success');
//   } catch (e) {
//     toast(`Invalid token: ${e.message}`, 'error');
//   }
// }

// /* ═══════════════════════════════════════════════════════════════
//    5. OVERVIEW — stats cards + latency bars
// ═══════════════════════════════════════════════════════════════ */

// async function refreshOverview() {
//   if (state.loading.stats) return;
//   state.loading.stats = true;

//   // Show skeleton values while loading
//   renderStatSkeletons();

//   const { ok, data, error } = await fetchStats();
//   state.loading.stats = false;

//   if (!ok) {
//     toast(error || 'Failed to load stats', 'error');
//     renderStatsError();
//     return;
//   }

//   state.data.stats = data;
//   renderStats(data);
//   renderLatency(data.tool_latency || []);
//   renderAllCharts(data);   // Phase 6 charts

//   // Fetch the 5 most recent violations for the overview preview panel.
//   // Separate lightweight call (limit=5) made only when overview is active.
//   // renderViolations on the Violations page is never involved.
//   const vRes = await fetchViolations(0, 5);
//   if (vRes.ok) renderOverviewViolations(vRes.data);
// }

// function renderStatSkeletons() {
//   const ids = ['stat-agents','stat-runs','stat-events','stat-violations','stat-rate','stat-completed','stat-avg-trust','stat-avg-risk'];
//   ids.forEach(id => {
//     const el = document.getElementById(id);
//     if (el) el.innerHTML = '<span class="skeleton" style="display:inline-block;width:48px;height:28px;"></span>';
//   });
// }

// function renderStatsError() {
//   const ids = ['stat-agents','stat-runs','stat-events','stat-violations','stat-rate','stat-completed','stat-avg-trust','stat-avg-risk'];
//   ids.forEach(id => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = '—';
//   });
// }

// function renderStats(s) {
//   const set = (id, val) => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = val;
//   };

//   set('stat-agents',    s.total_agents);
//   set('stat-runs',      s.total_runs);
//   set('stat-events',    s.total_events);
//   set('stat-violations', s.total_violations);
//   set('stat-rate',      `${s.violation_rate.toFixed(1)}%`);
//   set('stat-completed', s.completed_runs);

//   // Sub-lines
//   const setSub = (id, val) => {
//     const el = document.getElementById(id);
//     if (el) el.textContent = val;
//   };
//   setSub('stat-runs-sub',       `${s.completed_runs} completed · ${s.failed_runs} failed`);
//   setSub('stat-violations-sub', s.total_tool_calls > 0
//     ? `out of ${s.total_tool_calls} tool calls`
//     : 'no tool calls yet');

//   // Trust score aggregates
//   if (s.average_trust_score !== undefined) {
//     set('stat-avg-trust', s.average_trust_score.toFixed(1));
//     const dist = s.trust_distribution || {};
//     const distParts = Object.entries(dist)
//       .filter(([,v]) => v > 0)
//       .map(([k, v]) => `${v} ${k}`)
//       .join(' · ');
//     setSub('stat-avg-trust-sub', distParts || 'no agents yet');
//   }

//   // Risk score aggregates
//   if (s.average_risk_score !== undefined) {
//     set('stat-avg-risk', s.average_risk_score.toFixed(1));
//     const rdist = s.risk_distribution || {};
//     const riskParts = Object.entries(rdist)
//       .filter(([,v]) => v > 0)
//       .map(([k, v]) => `${v} ${k}`)
//       .join(' · ');
//     setSub('stat-avg-risk-sub', riskParts || 'no agents yet');
//   }
// }

// function renderLatency(tools) {
//   const grid = document.getElementById('latency-grid');
//   if (!grid) return;

//   if (!tools || tools.length === 0) {
//     grid.innerHTML = `
//       <div class="empty-state" style="padding:24px; grid-column:1/-1;">
//         <span class="empty-icon">⏱</span>
//         <span class="empty-title">No tool executions yet</span>
//         <span class="empty-body">Run an agent with a prompt to see latency data here.</span>
//       </div>`;
//     return;
//   }

//   const maxMs = Math.max(...tools.map(t => t.p95_ms || t.avg_ms || 1), 1);

//   grid.innerHTML = tools.map(t => {
//     const avgPct = Math.min(100, ((t.avg_ms || 0) / maxMs) * 100).toFixed(1);
//     const p95Pct = Math.min(100, ((t.p95_ms || 0) / maxMs) * 100).toFixed(1);

//     return `
//       <div class="latency-card">
//         <div class="latency-header">
//           <span class="latency-tool-name">${escHtml(t.tool_name)}</span>
//           <span class="latency-calls">${t.call_count} calls</span>
//         </div>
//         <div class="latency-bar-row">
//           <span class="latency-bar-label">avg</span>
//           <div class="latency-bar-track">
//             <div class="latency-bar-fill" style="width:${avgPct}%"></div>
//           </div>
//           <span class="latency-val">${t.avg_ms != null ? t.avg_ms.toFixed(1) + 'ms' : '—'}</span>
//         </div>
//         <div class="latency-bar-row">
//           <span class="latency-bar-label">p95</span>
//           <div class="latency-bar-track">
//             <div class="latency-bar-fill p95" style="width:${p95Pct}%"></div>
//           </div>
//           <span class="latency-val">${t.p95_ms != null ? t.p95_ms.toFixed(1) + 'ms' : '—'}</span>
//         </div>
//       </div>`;
//   }).join('');
// }

// /* ═══════════════════════════════════════════════════════════════
//    5b. OVERVIEW VIOLATIONS PREVIEW
// ═══════════════════════════════════════════════════════════════ */

// function renderOverviewViolations(data) {
//   const tbody   = document.getElementById('overview-violations-tbody');
//   const counter = document.getElementById('overview-violations-count');
//   if (!tbody) return;

//   if (counter) counter.textContent = data.total;

//   if (!data.violations || data.violations.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="5">
//         <div class="empty-state" style="padding:20px;">
//           <span class="empty-icon">✅</span>
//           <span class="empty-title">No violations yet</span>
//         </div>
//       </td></tr>`;
//     return;
//   }

//   tbody.innerHTML = data.violations.slice(0, 5).map(v => {
//     const inputStr = v.attempted_input
//       ? JSON.stringify(v.attempted_input).slice(0, 50)
//       : '—';
//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(v.timestamp)}</td>
//         <td>${escHtml(v.agent_name || '—')}</td>
//         <td><span class="badge badge-tool">${escHtml(v.tool_name || '—')}</span></td>
//         <td>
//           <span class="violation-input"
//                 style="font-family:var(--font-mono);font-size:11.5px;">
//             ${escHtml(inputStr)}
//           </span>
//         </td>
//         <td style="font-size:12px;color:var(--text-secondary);
//                    max-width:180px;overflow:hidden;
//                    text-overflow:ellipsis;white-space:nowrap;">
//           ${escHtml((v.denial_message || '').slice(0, 60))}
//         </td>
//       </tr>`;
//   }).join('');
// }

// /* ═══════════════════════════════════════════════════════════════
//    6. VIOLATIONS TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshViolations() {
//   if (state.loading.violations) return;
//   state.loading.violations = true;
//   setTableLoading('violations-tbody');

//   const skip  = state.filters.violationPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchViolations(skip, state.filters.pageSize);
//   state.loading.violations = false;

//   if (!ok) {
//     toast(error || 'Failed to load violations', 'error');
//     return;
//   }

//   state.data.violations = data;
//   renderViolations(data);
// }

// function renderViolations(data) {
//   const tbody   = document.getElementById('violations-tbody');
//   const counter = document.getElementById('violations-count');
//   if (counter) counter.textContent = data.total;

//   if (!data.violations || data.violations.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="6">
//         <div class="empty-state">
//           <span class="empty-icon">✅</span>
//           <span class="empty-title">No governance violations</span>
//           <span class="empty-body">All tool calls are within permitted bounds. Register an agent with limited tools and run a prompt to see violations here.</span>
//         </div>
//       </td></tr>`;
//     renderPagination('violations-pagination', data, state.filters.violationPage, (p) => {
//       state.filters.violationPage = p;
//       refreshViolations();
//     });
//     return;
//   }

//   tbody.innerHTML = data.violations.map(v => {
//     const inputStr = v.attempted_input
//       ? JSON.stringify(v.attempted_input)
//       : '—';

//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(v.timestamp)}</td>
//         <td>
//           <span class="badge badge-violation">⛔ violation</span>
//         </td>
//         <td>
//           <span style="font-weight:500;">${escHtml(v.agent_name || '—')}</span>
//           <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(v.agent_id.slice(0,8))}…</span>
//         </td>
//         <td><span class="badge badge-tool">${escHtml(v.tool_name || '—')}</span></td>
//         <td>
//           <div class="violation-detail">
//             <span class="violation-input">${escHtml(inputStr)}</span>
//           </div>
//         </td>
//         <td>
//           <div style="max-width:220px; font-size:12px; color:var(--text-secondary); line-height:1.5;">
//             ${escHtml(v.denial_message || '—')}
//           </div>
//         </td>
//       </tr>`;
//   }).join('');

//   renderPagination('violations-pagination', data, state.filters.violationPage, (p) => {
//     state.filters.violationPage = p;
//     refreshViolations();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    7. AUDIT LOG TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshAuditLog() {
//   if (state.loading.audit) return;
//   state.loading.audit = true;
//   setTableLoading('audit-tbody');

//   const skip = state.filters.auditPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchAuditLogs(
//     skip,
//     state.filters.pageSize,
//     state.filters.auditEventType,
//   );
//   state.loading.audit = false;

//   if (!ok) {
//     toast(error || 'Failed to load audit log', 'error');
//     return;
//   }

//   state.data.audit = data;
//   renderAuditLog(data);
// }

// function renderAuditLog(data) {
//   const tbody   = document.getElementById('audit-tbody');
//   const counter = document.getElementById('audit-count');
//   if (counter) counter.textContent = data.total;

//   if (!data.events || data.events.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="7">
//         <div class="empty-state">
//           <span class="empty-icon">📋</span>
//           <span class="empty-title">No audit events</span>
//           <span class="empty-body">Submit a run via POST /agents/run to populate the audit log.</span>
//         </div>
//       </td></tr>`;
//     renderPagination('audit-pagination', data, state.filters.auditPage, (p) => {
//       state.filters.auditPage = p;
//       refreshAuditLog();
//     });
//     return;
//   }

//   tbody.innerHTML = data.events.map(e => {
//     const inputSummary = e.input_data
//       ? JSON.stringify(e.input_data).slice(0, 60) + (JSON.stringify(e.input_data).length > 60 ? '…' : '')
//       : '—';

//     const permittedBadge = e.permitted === true
//       ? '<span class="badge badge-permitted">✓ allowed</span>'
//       : e.permitted === false
//         ? '<span class="badge badge-blocked">⛔ blocked</span>'
//         : '<span style="color:var(--text-muted);font-size:12px;">—</span>';

//     return `
//       <tr>
//         <td class="td-mono td-dim">${relativeTime(e.timestamp)}</td>
//         <td>${eventTypeBadge(e.event_type)}</td>
//         <td>
//           <span style="font-weight:500;">${escHtml(e.agent_name || '—')}</span>
//         </td>
//         <td>
//           ${e.tool_name
//             ? `<span class="badge badge-tool">${escHtml(e.tool_name)}</span>`
//             : '<span class="td-dim">—</span>'
//           }
//         </td>
//         <td>${permittedBadge}</td>
//         <td>
//           <div class="td-truncate td-mono" style="font-size:11.5px; color:var(--text-secondary);">
//             ${escHtml(inputSummary)}
//           </div>
//         </td>
//         <td>
//           ${e.latency_ms != null
//             ? `<span class="td-mono" style="font-size:12px; color:var(--accent-teal);">${e.latency_ms.toFixed(1)}ms</span>`
//             : '<span class="td-dim">—</span>'
//           }
//         </td>
//       </tr>`;
//   }).join('');

//   renderPagination('audit-pagination', data, state.filters.auditPage, (p) => {
//     state.filters.auditPage = p;
//     refreshAuditLog();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    7b. AGENT INTERACTIONS TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshInteractions() {
//   if (state.loading.interactions) return;
//   state.loading.interactions = true;
//   setTableLoading('interactions-tbody');

//   const skip  = state.filters.interactionsPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchInteractions(skip, state.filters.pageSize);
//   state.loading.interactions = false;

//   if (!ok) {
//     toast(error || 'Failed to load interactions', 'error');
//     return;
//   }

//   state.data.interactions = data;
//   renderInteractions(data);
// }

// function renderInteractions(data) {
//   const tbody   = document.getElementById('interactions-tbody');
//   const counter = document.getElementById('interactions-count');
//   if (counter) counter.textContent = data.total;

//   if (!data.interactions || data.interactions.length === 0) {
//     tbody.innerHTML = `
//       <tr><td colspan="5">
//         <div class="empty-state">
//           <span class="empty-icon">🔗</span>
//           <span class="empty-title">No agent interactions yet</span>
//           <span class="empty-body">
//             Use POST /agent-interactions to record a handoff, delegation,
//             request, or response between two registered agents.
//           </span>
//         </div>
//       </td></tr>`;
//     renderPagination('interactions-pagination', data, state.filters.interactionsPage, (p) => {
//       state.filters.interactionsPage = p;
//       refreshInteractions();
//     });
//     return;
//   }

//   const typeBadge = (type) => {
//     const map = {
//       'handoff':    '<span class="badge badge-handoff">⇒ handoff</span>',
//       'delegation': '<span class="badge badge-delegation">↓ delegation</span>',
//       'request':    '<span class="badge badge-request">? request</span>',
//       'response':   '<span class="badge badge-response">✓ response</span>',
//     };
//     return map[type] || `<span class="badge">${escHtml(type)}</span>`;
//   };

//   tbody.innerHTML = data.interactions.map(i => `
//     <tr>
//       <td class="td-mono td-dim">${relativeTime(i.created_at)}</td>
//       <td>${typeBadge(i.interaction_type)}</td>
//       <td>
//         <span style="font-weight:500;">${escHtml(i.source_agent_name || '—')}</span>
//         <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(i.source_agent_id.slice(0,8))}…</span>
//       </td>
//       <td>
//         <span style="font-weight:500;">${escHtml(i.target_agent_name || '—')}</span>
//         <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(i.target_agent_id.slice(0,8))}…</span>
//       </td>
//       <td>
//         <div style="max-width:260px; font-size:12px; color:var(--text-secondary);
//                     overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
//           ${i.message ? escHtml(i.message) : '<span class="td-dim">—</span>'}
//         </div>
//       </td>
//     </tr>`).join('');

//   renderPagination('interactions-pagination', data, state.filters.interactionsPage, (p) => {
//     state.filters.interactionsPage = p;
//     refreshInteractions();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    7c. POLICIES TABLE
// ═══════════════════════════════════════════════════════════════ */

// async function refreshPolicies() {
//   if (state.loading.policies) return;
//   state.loading.policies = true;
//   setTableLoading('policies-tbody');
//   const skip  = state.filters.policiesPage * state.filters.pageSize;
//   const { ok, data, error } = await fetchPolicies(skip, state.filters.pageSize);
//   state.loading.policies = false;
//   if (!ok) { toast(error || 'Failed to load policies', 'error'); return; }
//   state.data.policies = data;
//   renderPolicies(data);
// }

// function renderPolicies(data) {
//   const tbody   = document.getElementById('policies-tbody');
//   const counter = document.getElementById('policies-count');
//   if (!tbody) return;
//   if (counter) counter.textContent = data.total;

//   if (!data.policies || data.policies.length === 0) {
//     tbody.innerHTML = `<tr><td colspan="7"><div class="empty-state">
//       <span class="empty-icon">🛡</span>
//       <span class="empty-title">No policies defined yet</span>
//       <span class="empty-body">Create policies via POST /policies.</span>
//     </div></td></tr>`;
//     renderPagination('policies-pagination', data, state.filters.policiesPage, (p) => {
//       state.filters.policiesPage = p; refreshPolicies();
//     });
//     return;
//   }

//   const severityBadge = (sev) => {
//     const map = {
//       'LOW':      '<span class="badge" style="background:rgba(63,185,80,0.15);color:var(--accent-green);">LOW</span>',
//       'MEDIUM':   '<span class="badge" style="background:rgba(227,135,62,0.15);color:var(--accent-orange);">MEDIUM</span>',
//       'HIGH':     '<span class="badge" style="background:rgba(248,81,73,0.15);color:var(--accent-red);">HIGH</span>',
//       'CRITICAL': '<span class="badge" style="background:rgba(188,140,255,0.15);color:var(--accent-purple);">CRITICAL</span>',
//     };
//     return map[sev] || `<span class="badge">${escHtml(sev)}</span>`;
//   };

//   const ruleTypeBadge = (rt) => {
//     const labels = {
//       'tool_allow':   '✓ tool_allow',
//       'tool_deny':    '⛔ tool_deny',
//       'rate_limit':   '⏱ rate_limit',
//       'prompt_guard': '🔍 prompt_guard',
//       'time_window':  '🕐 time_window',
//     };
//     return `<span class="badge badge-tool">${escHtml(labels[rt] || rt)}</span>`;
//   };

//   tbody.innerHTML = data.policies.map(p => `
//     <tr>
//       <td><span style="font-weight:600;">${escHtml(p.name)}</span>
//         ${p.description ? `<br><span class="td-dim" style="font-size:11px;">${escHtml(p.description.slice(0,50))}</span>` : ''}
//       </td>
//       <td>${ruleTypeBadge(p.rule_type)}</td>
//       <td>${severityBadge(p.severity)}</td>
//       <td>${p.is_active
//           ? '<span class="badge badge-permitted">✓ active</span>'
//           : '<span class="badge badge-run-end">○ inactive</span>'}</td>
//       <td><span class="td-mono" style="font-size:12px;">${p.agent_count}</span></td>
//       <td><div class="td-truncate td-mono" style="font-size:11px;max-width:180px;color:var(--text-secondary);">
//         ${escHtml(JSON.stringify(p.rule_config))}
//       </div></td>
//       <td class="td-mono td-dim">${relativeTime(p.created_at)}</td>
//     </tr>`).join('');

//   renderPagination('policies-pagination', data, state.filters.policiesPage, (p) => {
//     state.filters.policiesPage = p; refreshPolicies();
//   });
// }

// /* ═══════════════════════════════════════════════════════════════
//    8. UTILITIES
// ═══════════════════════════════════════════════════════════════ */

// function escHtml(str) {
//   if (str == null) return '';
//   return String(str)
//     .replace(/&/g, '&amp;')
//     .replace(/</g, '&lt;')
//     .replace(/>/g, '&gt;')
//     .replace(/"/g, '&quot;');
// }

// function relativeTime(isoString) {
//   if (!isoString) return '—';
//   const date  = new Date(isoString.endsWith('Z') ? isoString : isoString + 'Z');
//   const diff  = (Date.now() - date.getTime()) / 1000;
//   if (diff < 5)   return 'just now';
//   if (diff < 60)  return `${Math.floor(diff)}s ago`;
//   if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
//   if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
//   return date.toLocaleDateString();
// }

// function eventTypeBadge(type) {
//   const map = {
//     'run_start': '<span class="badge badge-run-start">▶ run_start</span>',
//     'tool_call': '<span class="badge badge-tool-call">⚙ tool_call</span>',
//     'tool_end':  '<span class="badge badge-tool-end">✓ tool_end</span>',
//     'violation': '<span class="badge badge-violation">⛔ violation</span>',
//     'run_end':   '<span class="badge badge-run-end">■ run_end</span>',
//     'agent_handoff':    '<span class="badge badge-handoff">⇒ agent_handoff</span>',
//     'policy_violation': '<span class="badge badge-violation">🛡 policy_violation</span>',
//   };
//   return map[type] || `<span class="badge">${escHtml(type)}</span>`;
// }

// function trustLevelBadge(level) {
//   const map = {
//     'TRUSTED':   '<span class="badge" style="background:rgba(63,185,80,0.15);color:#3fb950;">🏅 TRUSTED</span>',
//     'MONITORED': '<span class="badge" style="background:rgba(79,142,247,0.15);color:#4f8ef7;">👁 MONITORED</span>',
//     'WARNING':   '<span class="badge" style="background:rgba(227,135,62,0.15);color:#e3873e;">⚠ WARNING</span>',
//     'HIGH_RISK': '<span class="badge" style="background:rgba(248,81,73,0.15);color:#f85149;">🔴 HIGH_RISK</span>',
//   };
//   return map[level] || `<span class="badge">${escHtml(level)}</span>`;
// }

// function formatTrustScore(score) {
//   if (score === null || score === undefined) return '—';
//   const color = score >= 90 ? '#3fb950'
//               : score >= 70 ? '#4f8ef7'
//               : score >= 50 ? '#e3873e'
//               : '#f85149';
//   return `<span style="font-family:var(--font-mono);font-weight:700;color:${color};">${score.toFixed(1)}</span>`;
// }

// function riskLevelBadge(level) {
//   const map = {
//     'SAFE':     '<span class="badge" style="background:rgba(63,185,80,0.15);color:#3fb950;">✅ SAFE</span>',
//     'LOW':      '<span class="badge" style="background:rgba(57,197,207,0.15);color:#39c5cf;">🔵 LOW</span>',
//     'MEDIUM':   '<span class="badge" style="background:rgba(227,135,62,0.15);color:#e3873e;">🟡 MEDIUM</span>',
//     'HIGH':     '<span class="badge" style="background:rgba(248,81,73,0.15);color:#f85149;">🔴 HIGH</span>',
//     'CRITICAL': '<span class="badge" style="background:rgba(248,81,73,0.25);color:#f85149;font-weight:700;">🚨 CRITICAL</span>',
//   };
//   return map[level] || `<span class="badge">${escHtml(level)}</span>`;
// }

// function formatRiskScore(score) {
//   if (score === null || score === undefined) return '—';
//   const color = score >= 75 ? '#f85149'
//               : score >= 50 ? '#e3873e'
//               : score >= 25 ? '#e3c93e'
//               : '#3fb950';
//   return `<span style="font-family:var(--font-mono);font-weight:700;color:${color};">${score.toFixed(1)}</span>`;
// }

// function setTableLoading(tbodyId) {
//   const el = document.getElementById(tbodyId);
//   if (!el) return;
//   el.innerHTML = Array.from({ length: 5 }, () => `
//     <tr>${Array.from({ length: 7 }, () =>
//       `<td><div class="skeleton" style="height:16px; width:${60 + Math.random()*60 | 0}px;"></div></td>`
//     ).join('')}</tr>
//   `).join('');
// }

// function renderPagination(containerId, data, currentPage, onPage) {
//   const el = document.getElementById(containerId);
//   if (!el) return;

//   const totalPages = Math.ceil(data.total / (data.limit || 25));
//   if (totalPages <= 1) { el.innerHTML = ''; return; }

//   const prevDisabled = currentPage === 0;
//   const nextDisabled = currentPage >= totalPages - 1;

//   el.innerHTML = `
//     <div style="display:flex; align-items:center; gap:10px; justify-content:flex-end; padding:12px 14px; border-top:1px solid var(--border);">
//       <span style="font-size:12px; color:var(--text-muted);">
//         ${data.skip + 1}–${Math.min(data.skip + data.limit, data.total)} of ${data.total}
//       </span>
//       <button class="btn btn-ghost btn-sm" ${prevDisabled ? 'disabled' : ''} onclick="(${onPage.toString()})(${currentPage - 1})">
//         ← Prev
//       </button>
//       <button class="btn btn-ghost btn-sm" ${nextDisabled ? 'disabled' : ''} onclick="(${onPage.toString()})(${currentPage + 1})">
//         Next →
//       </button>
//     </div>`;
// }

// function updateConnectionStatus(status, label) {
//   const dot  = document.getElementById('status-dot');
//   const text = document.getElementById('status-text');
//   if (dot)  { dot.className = `status-dot ${status}`; }
//   if (text) { text.textContent = label || status; }
// }

// function updateLastRefresh() {
//   state.lastRefresh = new Date();
//   const el = document.getElementById('last-refresh');
//   if (el) el.textContent = `Refreshed ${relativeTime(state.lastRefresh.toISOString())}`;
// }

// let _refreshLabelTimer = null;
// function startRefreshLabelUpdater() {
//   clearInterval(_refreshLabelTimer);
//   _refreshLabelTimer = setInterval(() => {
//     if (!state.lastRefresh) return;
//     const el = document.getElementById('last-refresh');
//     if (el) el.textContent = `Refreshed ${relativeTime(state.lastRefresh.toISOString())}`;
//   }, 15_000);
// }

// /* Toast notifications */
// function toast(message, type = 'info') {
//   const container = document.getElementById('toast-container');
//   if (!container) return;

//   const icons = { error: '⚠', success: '✓', info: 'ℹ' };
//   const el    = document.createElement('div');
//   el.className = `toast ${type}`;
//   el.innerHTML = `<span>${icons[type] || icons.info}</span><span>${escHtml(message)}</span>`;
//   container.appendChild(el);

//   setTimeout(() => {
//     el.style.animation = 'toast-out 0.2s ease forwards';
//     setTimeout(() => el.remove(), 200);
//   }, 3500);
// }

// /* ═══════════════════════════════════════════════════════════════
//    8b. CHARTS (Chart.js — Phase 6)
// ═══════════════════════════════════════════════════════════════ */

// const _charts = {};

// function _destroyChart(id) {
//   if (_charts[id]) {
//     _charts[id].destroy();
//     delete _charts[id];
//   }
// }

// const _CHART_DEFAULTS = {
//   font: { family: 'Inter, system-ui, sans-serif', size: 12 },
//   color: '#8b9ab4',
// };

// const _TRUST_COLORS = ['#3fb950','#4f8ef7','#e3873e','#f85149'];
// const _RISK_COLORS  = ['#3fb950','#39c5cf','#e3c93e','#f85149','#b80000'];
// const _INTER_COLORS = ['#4f8ef7','#bc8cff','#e3873e','#39c5cf'];
// const _TOOL_COLORS  = ['#4f8ef7','#39c5cf','#3fb950','#bc8cff',
//                        '#e3873e','#f85149','#e3c93e','#8b9ab4',
//                        '#ff7eb3','#43c4e8'];

// function _showChartEmpty(id, show) {
//   const empty  = document.getElementById('chart-' + id + '-empty');
//   const canvas = document.getElementById('chart-' + id);
//   if (!empty || !canvas) return;
//   if (show) {
//     empty.style.display  = 'flex';
//     canvas.style.display = 'none';
//   } else {
//     empty.style.display  = 'none';
//     canvas.style.display = 'block';
//   }
// }

// function renderTrustChart(trustDist) {
//   const id = 'trust';
//   _destroyChart(id);
//   const labels = ['TRUSTED','MONITORED','WARNING','HIGH_RISK'];
//   const values = labels.map(l => (trustDist || {})[l] || 0);
//   if (values.every(v => v === 0)) { _showChartEmpty(id, true); return; }
//   _showChartEmpty(id, false);
//   const ctx = document.getElementById('chart-' + id).getContext('2d');
//   _charts[id] = new Chart(ctx, {
//     type: 'doughnut',
//     data: {
//       labels,
//       datasets: [{ data: values, backgroundColor: _TRUST_COLORS,
//                    borderColor: '#1c2333', borderWidth: 2, hoverOffset: 6 }],
//     },
//     options: {
//       responsive: true, maintainAspectRatio: false,
//       plugins: {
//         legend: { position: 'right',
//                   labels: { color: '#8b9ab4', font: _CHART_DEFAULTS.font, boxWidth: 14 } },
//         tooltip: { callbacks: { label: c => ' ' + c.label + ': ' + c.raw + (c.raw !== 1 ? ' agents' : ' agent') } },
//       },
//     },
//   });
// }

// function renderRiskChart(riskDist) {
//   const id = 'risk';
//   _destroyChart(id);
//   const labels = ['SAFE','LOW','MEDIUM','HIGH','CRITICAL'];
//   const values = labels.map(l => (riskDist || {})[l] || 0);
//   if (values.every(v => v === 0)) { _showChartEmpty(id, true); return; }
//   _showChartEmpty(id, false);
//   const ctx = document.getElementById('chart-' + id).getContext('2d');
//   _charts[id] = new Chart(ctx, {
//     type: 'doughnut',
//     data: {
//       labels,
//       datasets: [{ data: values, backgroundColor: _RISK_COLORS,
//                    borderColor: '#1c2333', borderWidth: 2, hoverOffset: 6 }],
//     },
//     options: {
//       responsive: true, maintainAspectRatio: false,
//       plugins: {
//         legend: { position: 'right',
//                   labels: { color: '#8b9ab4', font: _CHART_DEFAULTS.font, boxWidth: 14 } },
//         tooltip: { callbacks: { label: c => ' ' + c.label + ': ' + c.raw + (c.raw !== 1 ? ' agents' : ' agent') } },
//       },
//     },
//   });
// }

// function renderInteractionChart(interactionsByType) {
//   const id = 'interactions';
//   _destroyChart(id);
//   const entries = Object.entries(interactionsByType || {}).filter(function(e) { return e[1] > 0; });
//   if (entries.length === 0) { _showChartEmpty(id, true); return; }
//   _showChartEmpty(id, false);
//   const labels = entries.map(function(e) { return e[0]; });
//   const values = entries.map(function(e) { return e[1]; });
//   const ctx = document.getElementById('chart-' + id).getContext('2d');
//   _charts[id] = new Chart(ctx, {
//     type: 'pie',
//     data: {
//       labels,
//       datasets: [{ data: values,
//                    backgroundColor: _INTER_COLORS.slice(0, labels.length),
//                    borderColor: '#1c2333', borderWidth: 2, hoverOffset: 6 }],
//     },
//     options: {
//       responsive: true, maintainAspectRatio: false,
//       plugins: {
//         legend: { position: 'right',
//                   labels: { color: '#8b9ab4', font: _CHART_DEFAULTS.font, boxWidth: 14 } },
//         tooltip: { callbacks: { label: c => ' ' + c.label + ': ' + c.raw } },
//       },
//     },
//   });
// }

// function renderToolUsageChart(toolLatency) {
//   const id = 'tools';
//   _destroyChart(id);
//   const data = (toolLatency || []).filter(function(t) { return t.call_count > 0; });
//   if (data.length === 0) { _showChartEmpty(id, true); return; }
//   _showChartEmpty(id, false);
//   const labels = data.map(function(t) { return t.tool_name; });
//   const values = data.map(function(t) { return t.call_count; });
//   const ctx = document.getElementById('chart-' + id).getContext('2d');
//   _charts[id] = new Chart(ctx, {
//     type: 'bar',
//     data: {
//       labels,
//       datasets: [{ label: 'Tool Calls', data: values,
//                    backgroundColor: _TOOL_COLORS.slice(0, labels.length),
//                    borderColor: '#1c2333', borderWidth: 1, borderRadius: 4 }],
//     },
//     options: {
//       responsive: true, maintainAspectRatio: false,
//       plugins: {
//         legend: { display: false },
//         tooltip: { callbacks: { label: c => ' ' + c.raw + (c.raw !== 1 ? ' calls' : ' call') } },
//       },
//       scales: {
//         x: { ticks: { color: '#8b9ab4', font: _CHART_DEFAULTS.font },
//              grid:  { color: 'rgba(42,52,80,0.5)' } },
//         y: { beginAtZero: true,
//              ticks: { color: '#8b9ab4', font: _CHART_DEFAULTS.font, precision: 0 },
//              grid:  { color: 'rgba(42,52,80,0.5)' } },
//       },
//     },
//   });
// }

// function renderViolationChart(totalViolations, totalPolicyViolations) {
//   const id = 'violations';
//   _destroyChart(id);
//   const gov = totalViolations || 0;
//   const pol = totalPolicyViolations || 0;
//   if (gov === 0 && pol === 0) { _showChartEmpty(id, true); return; }
//   _showChartEmpty(id, false);
//   const ctx = document.getElementById('chart-' + id).getContext('2d');
//   _charts[id] = new Chart(ctx, {
//     type: 'bar',
//     data: {
//       labels: ['Governance Violations', 'Policy Violations'],
//       datasets: [{ label: 'Count', data: [gov, pol],
//                    backgroundColor: ['#f85149','#e3873e'],
//                    borderColor: '#1c2333', borderWidth: 1,
//                    borderRadius: 4, barThickness: 48 }],
//     },
//     options: {
//       responsive: true, maintainAspectRatio: false,
//       plugins: {
//         legend: { display: false },
//         tooltip: { callbacks: { label: c => ' ' + c.raw + (c.raw !== 1 ? ' violations' : ' violation') } },
//       },
//       scales: {
//         x: { ticks: { color: '#8b9ab4', font: _CHART_DEFAULTS.font },
//              grid:  { color: 'rgba(42,52,80,0.5)' } },
//         y: { beginAtZero: true,
//              ticks: { color: '#8b9ab4', font: _CHART_DEFAULTS.font, precision: 0 },
//              grid:  { color: 'rgba(42,52,80,0.5)' } },
//       },
//     },
//   });
// }

// function renderAllCharts(statsData) {
//   renderTrustChart(statsData.trust_distribution || {});
//   renderRiskChart(statsData.risk_distribution || {});
//   renderInteractionChart(statsData.interactions_by_type || {});
//   renderToolUsageChart(statsData.tool_latency || []);
//   renderViolationChart(
//     statsData.total_violations || 0,
//     statsData.total_policy_violations || 0
//   );
// }

// /* ═══════════════════════════════════════════════════════════════
//    9. AUTO-REFRESH + INIT
// ═══════════════════════════════════════════════════════════════ */

// const REFRESH_INTERVAL_MS = 30_000;

// function refreshCurrentView() {
//   if (!state.token) return;

//   const btn = document.getElementById('btn-refresh');
//   if (btn) {
//     btn.classList.add('btn-spinning');
//     btn.querySelector('.btn-icon').style.animation = 'spin 0.8s linear infinite';
//   }

//   const views = {
//     overview:     refreshOverview,
//     violations:   refreshViolations,
//     audit:        refreshAuditLog,
//     interactions: refreshInteractions,
//     policies:     refreshPolicies,
//   };
//   const fn = views[state.currentView];
//   if (fn) fn().finally(() => {
//     updateLastRefresh();
//     if (btn) btn.querySelector('.btn-icon').style.animation = '';
//   });
// }

// function startAutoRefresh() {
//   stopAutoRefresh();
//   state.refreshTimer = setInterval(refreshCurrentView, REFRESH_INTERVAL_MS);
// }

// function stopAutoRefresh() {
//   if (state.refreshTimer) clearInterval(state.refreshTimer);
//   state.refreshTimer = null;
// }

// /* ── Boot ─────────────────────────────────────────────────────── */
// document.addEventListener('DOMContentLoaded', () => {

//   // Nav click handlers
//   document.querySelectorAll('.nav-item[data-view]').forEach(el => {
//     el.addEventListener('click', () => navigateTo(el.dataset.view));
//   });

//   // Refresh button
//   document.getElementById('btn-refresh')?.addEventListener('click', refreshCurrentView);

//   // Disconnect button
//   document.getElementById('btn-disconnect')?.addEventListener('click', () => {
//     clearToken();
//     toast('Disconnected', 'info');
//   });

//   // Register form
//   document.getElementById('btn-register')?.addEventListener('click', handleRegister);

//   // Paste token
//   document.getElementById('btn-use-token')?.addEventListener('click', handlePasteToken);

//   // Audit event type filter
//   document.getElementById('audit-filter-type')?.addEventListener('change', (e) => {
//     state.filters.auditEventType = e.target.value;
//     state.filters.auditPage      = 0;
//     refreshAuditLog();
//   });

//   // ── Run Agent panel ───────────────────────────────────────
//   document.getElementById('btn-run-agent')?.addEventListener('click', async () => {
//     const prompt = document.getElementById('run-prompt')?.value?.trim();
//     const statusEl = document.getElementById('run-status');
//     const resultEl = document.getElementById('run-result');

//     if (!state.token) {
//       return toast('Please register or connect an agent first.', 'error');
//     }
//     if (!prompt) {
//       return toast('Please enter a prompt.', 'error');
//     }

//     const btn = document.getElementById('btn-run-agent');
//     btn.disabled = true;
//     btn.textContent = '⟳ Running…';
//     if (statusEl) statusEl.textContent = 'Sending to agent…';
//     if (resultEl) resultEl.style.display = 'none';

//     const { ok, data, error } = await apiFetch('/runs', {
//       method: 'POST',
//       body: JSON.stringify({ prompt }),
//     });

//     btn.disabled = false;
//     btn.textContent = '▶ Run Agent';

//     if (!ok) {
//       if (statusEl) statusEl.textContent = '';
//       toast(error || 'Run failed', 'error');
//       return;
//     }

//     const violations = data.violation_count || 0;
//     if (statusEl) statusEl.textContent =
//       `✓ Completed in ${data.latency_ms ? data.latency_ms.toFixed(0) + 'ms' : '?ms'} · ${violations} violation${violations !== 1 ? 's' : ''}`;

//     if (resultEl) {
//       resultEl.style.display = 'block';
//       resultEl.innerHTML =
//         `<strong style="color:var(--text-primary);">Result:</strong><br>${escHtml(data.result || '—')}`;
//     }

//     // Refresh dashboard stats so charts and cards update immediately
//     setTimeout(() => refreshOverview(), 1000);
//   });

//   // Mobile sidebar toggle
//   document.getElementById('mobile-menu-btn')?.addEventListener('click', () => {
//     document.querySelector('.sidebar')?.classList.toggle('open');
//   });

//   // Start relative-time label updater
//   startRefreshLabelUpdater();

//   // Restore session or show auth banner
//   if (!tryRestoreSession()) {
//     showAuthBanner();
//     updateConnectionStatus('disconnected', 'Not connected');
//   }
// });




































/**
 * app.js — AgentWatch Dashboard
 *
 * Architecture:
 *   State → API → Render pipeline.
 *   No framework, no bundler.  Vanilla ES2022 modules pattern
 *   (everything in one file for simplicity — split if the project grows).
 *
 * Sections:
 *   1. State management
 *   2. API client (all fetch calls, token injection)
 *   3. Navigation / view router
 *   4. Auth flow (register / paste token)
 *   5. Render: Overview (stats + latency)
 *   6. Render: Violations table
 *   7. Render: Audit log table
 *   8. Utilities (badges, timestamps, toast)
 *   9. Auto-refresh + init
 */

'use strict';

/* ═══════════════════════════════════════════════════════════════
   1. STATE
═══════════════════════════════════════════════════════════════ */

const state = {
  token:           null,       // JWT bearer token
  agentId:         null,       // UUID of the authenticated agent
  agentName:       null,
  lastRefresh:     null,
  refreshTimer:    null,
  currentView:     'overview',
  loading: {
    stats:        false,
    violations:   false,
    audit:        false,
    interactions: false,
    policies:     false,
  },
  data: {
    stats:        null,
    violations:   null,    // { violations: [], total, skip, limit }
    audit:        null,    // { events: [], total, skip, limit }
    interactions: null,    // { interactions: [], total, skip, limit }
    policies:     null,    // { policies: [], total, skip, limit }
  },
  filters: {
    auditEventType:    '',
    violationPage:     0,
    auditPage:         0,
    interactionsPage:  0,
    policiesPage:      0,
    pageSize:          25,
  },
};

/* ═══════════════════════════════════════════════════════════════
   2. API CLIENT
═══════════════════════════════════════════════════════════════ */

const API_BASE = '';  // Same origin — served by FastAPI at /dashboard/

function authHeaders() {
  return {
    'Content-Type': 'application/json',
    ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
  };
}

/**
 * Generic fetch wrapper.
 * Returns { ok: true, data } or { ok: false, error, status }.
 */
async function apiFetch(path, opts = {}) {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...opts,
      headers: { ...authHeaders(), ...(opts.headers || {}) },
    });
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      return { ok: false, error: data?.detail || `HTTP ${res.status}`, status: res.status };
    }
    return { ok: true, data };
  } catch (err) {
    return { ok: false, error: err.message || 'Network error', status: 0 };
  }
}

async function fetchStats() {
  return apiFetch('/analytics/stats');
}

async function fetchViolations(skip = 0, limit = 25) {
  return apiFetch(`/governance/violations?skip=${skip}&limit=${limit}`);
}

async function fetchInteractions(skip = 0, limit = 25) {
  return apiFetch(`/agent-interactions?skip=${skip}&limit=${limit}`);
}

async function fetchTrust() {
  return apiFetch('/analytics/trust');
}

async function fetchAgentTrust(agentId) {
  return apiFetch(`/analytics/trust/${agentId}`);
}

async function fetchRisk() {
  return apiFetch('/analytics/risk');
}

async function fetchAgentRisk(agentId) {
  return apiFetch(`/analytics/risk/${agentId}`);
}

async function fetchAuditLogs(skip = 0, limit = 25, eventType = '') {
  const et = eventType ? `&event_type=${encodeURIComponent(eventType)}` : '';
  return apiFetch(`/audit/logs?skip=${skip}&limit=${limit}${et}`);
}

async function fetchPolicies(skip = 0, limit = 25) {
  return apiFetch(`/policies?skip=${skip}&limit=${limit}`);
}

async function createPolicy(payload) {
  return apiFetch('/policies', { method: 'POST', body: JSON.stringify(payload) });
}

async function assignPolicyToAgent(policyId, agentId) {
  return apiFetch(`/policies/${policyId}/agents/${agentId}`, { method: 'POST' });
}

// Config hints per rule type
const _POLICY_CONFIG_HINTS = {
  'prompt_guard': '{"blocked_keywords":["password","hack","secret"]}',
  'tool_deny':    '{"tool":"weather"}',
  'tool_allow':   '{"tool":"calculator"}',
  'rate_limit':   '{"max_calls_per_run":3}',
  'time_window':  '{"start_hour":9,"end_hour":18}',
};

async function registerAgent(name, secret, tools) {
  return apiFetch('/agents/register', {
    method: 'POST',
    body: JSON.stringify({ name, secret, allowed_tools: tools }),
  });
}

/* ═══════════════════════════════════════════════════════════════
   3. NAVIGATION
═══════════════════════════════════════════════════════════════ */

function navigateTo(viewId) {
  state.currentView = viewId;

  // Update sidebar active state
  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.view === viewId);
  });

  // Swap visible view
  document.querySelectorAll('.view').forEach(el => {
    el.classList.toggle('active', el.id === `view-${viewId}`);
  });

  // Update topbar title
  const titles = {
    overview:     { title: 'Overview',              sub: 'Platform health at a glance' },
    violations:   { title: 'Governance Violations', sub: 'Blocked tool calls and policy enforcement' },
    audit:        { title: 'Audit Log',             sub: 'Full event trace across all agent runs' },
    interactions: { title: 'Agent Interactions',    sub: 'Agent-to-agent communication records' },
    policies:     { title: 'Governance Policies',   sub: 'Named rules enforced before every run' },
  };
  const t = titles[viewId] || {};
  document.getElementById('topbar-title').textContent    = t.title || '';
  document.getElementById('topbar-subtitle').textContent = t.sub   || '';

  // Load data for the view
  if (!state.token) return;
  if (viewId === 'overview')     refreshOverview();
  if (viewId === 'violations')   refreshViolations();
  if (viewId === 'audit')        refreshAuditLog();
  if (viewId === 'interactions') refreshInteractions();
  if (viewId === 'policies')     refreshPolicies();
}

/* ═══════════════════════════════════════════════════════════════
   4. AUTH FLOW
═══════════════════════════════════════════════════════════════ */

function setToken(token, agentId, agentName) {
  state.token     = token;
  state.agentId   = agentId;
  state.agentName = agentName || 'unknown';

  // Persist in sessionStorage (not localStorage — clears on tab close)
  sessionStorage.setItem('aw_token',      token);
  sessionStorage.setItem('aw_agent_id',   agentId);
  sessionStorage.setItem('aw_agent_name', agentName || '');

  updateConnectionStatus('connected', agentName || agentId?.slice(0, 8) + '…');
  hideAuthBanner();
  startAutoRefresh();
  // Pre-fill assign agent ID field with connected agent
  const _assignInput = document.getElementById('assign-agent-id');
  if (_assignInput) _assignInput.value = agentId || '';
  navigateTo(state.currentView);
}

function clearToken() {
  state.token = state.agentId = state.agentName = null;
  sessionStorage.removeItem('aw_token');
  sessionStorage.removeItem('aw_agent_id');
  sessionStorage.removeItem('aw_agent_name');
  stopAutoRefresh();
  updateConnectionStatus('disconnected', 'Not connected');
  showAuthBanner();
}

function tryRestoreSession() {
  const token     = sessionStorage.getItem('aw_token');
  const agentId   = sessionStorage.getItem('aw_agent_id');
  const agentName = sessionStorage.getItem('aw_agent_name');
  if (token && agentId) {
    setToken(token, agentId, agentName);
    return true;
  }
  return false;
}

function showAuthBanner() {
  document.getElementById('auth-banner').style.display = 'block';
}

function hideAuthBanner() {
  document.getElementById('auth-banner').style.display = 'none';
}

async function handleRegister() {
  const name   = document.getElementById('reg-name').value.trim();
  const secret = document.getElementById('reg-secret').value;
  const tools  = Array.from(
    document.querySelectorAll('.tool-checkbox:checked')
  ).map(el => el.value);

  if (!name || name.length < 2) {
    return toast('Agent name must be at least 2 characters', 'error');
  }
  if (!secret || secret.length < 12) {
    return toast('Secret must be at least 12 characters', 'error');
  }

  const btn = document.getElementById('btn-register');
  btn.disabled = true;
  btn.innerHTML = '<span class="btn-icon btn-spinning">⟳</span> Registering…';

  const { ok, data, error } = await registerAgent(name, secret, tools);

  btn.disabled = false;
  btn.innerHTML = '🚀 Register Agent';

  if (!ok) {
    toast(error || 'Registration failed', 'error');
    return;
  }

  toast(`Agent "${name}" registered successfully!`, 'success');
  setToken(data.access_token, data.agent_id, name);
  document.getElementById('token-display').value = data.access_token;
}

async function handlePasteToken() {
  const raw = document.getElementById('token-display').value.trim();
  if (!raw) return toast('Paste a JWT token first', 'error');

  // Decode payload to extract sub (agent_id) and agent_name — no library needed
  try {
    const parts = raw.split('.');
    if (parts.length !== 3) throw new Error('Not a JWT');
    const payload = JSON.parse(atob(parts[1].replace(/-/g,'+').replace(/_/g,'/')));
    if (!payload.sub) throw new Error('Token has no sub claim');
    setToken(raw, payload.sub, payload.agent_name || payload.sub.slice(0, 8) + '…');
    toast('Token applied successfully', 'success');
  } catch (e) {
    toast(`Invalid token: ${e.message}`, 'error');
  }
}

/* ═══════════════════════════════════════════════════════════════
   5. OVERVIEW — stats cards + latency bars
═══════════════════════════════════════════════════════════════ */

async function refreshOverview() {
  if (state.loading.stats) return;
  state.loading.stats = true;

  // Show skeleton values while loading
  renderStatSkeletons();

  const { ok, data, error } = await fetchStats();
  state.loading.stats = false;

  if (!ok) {
    toast(error || 'Failed to load stats', 'error');
    renderStatsError();
    return;
  }

  state.data.stats = data;
  renderStats(data);
  renderLatency(data.tool_latency || []);
  renderAllCharts(data);   // Phase 6 charts

  // Fetch the 5 most recent violations for the overview preview panel.
  // Separate lightweight call (limit=5) made only when overview is active.
  // renderViolations on the Violations page is never involved.
  const vRes = await fetchViolations(0, 5);
  if (vRes.ok) renderOverviewViolations(vRes.data);
}

function renderStatSkeletons() {
  const ids = ['stat-agents','stat-runs','stat-events','stat-violations','stat-rate','stat-completed','stat-avg-trust','stat-avg-risk'];
  ids.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = '<span class="skeleton" style="display:inline-block;width:48px;height:28px;"></span>';
  });
}

function renderStatsError() {
  const ids = ['stat-agents','stat-runs','stat-events','stat-violations','stat-rate','stat-completed','stat-avg-trust','stat-avg-risk'];
  ids.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = '—';
  });
}

function renderStats(s) {
  const set = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  };

  set('stat-agents',    s.total_agents);
  set('stat-runs',      s.total_runs);
  set('stat-events',    s.total_events);
  set('stat-violations', s.total_violations);
  set('stat-rate',      `${s.violation_rate.toFixed(1)}%`);
  set('stat-completed', s.completed_runs);

  // Sub-lines
  const setSub = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  };
  setSub('stat-runs-sub',       `${s.completed_runs} completed · ${s.failed_runs} failed`);
  setSub('stat-violations-sub', s.total_tool_calls > 0
    ? `out of ${s.total_tool_calls} tool calls`
    : 'no tool calls yet');

  // Trust score aggregates
  if (s.average_trust_score !== undefined) {
    set('stat-avg-trust', s.average_trust_score.toFixed(1));
    const dist = s.trust_distribution || {};
    const distParts = Object.entries(dist)
      .filter(([,v]) => v > 0)
      .map(([k, v]) => `${v} ${k}`)
      .join(' · ');
    setSub('stat-avg-trust-sub', distParts || 'no agents yet');
  }

  // Risk score aggregates
  if (s.average_risk_score !== undefined) {
    set('stat-avg-risk', s.average_risk_score.toFixed(1));
    const rdist = s.risk_distribution || {};
    const riskParts = Object.entries(rdist)
      .filter(([,v]) => v > 0)
      .map(([k, v]) => `${v} ${k}`)
      .join(' · ');
    setSub('stat-avg-risk-sub', riskParts || 'no agents yet');
  }
}

function renderLatency(tools) {
  const grid = document.getElementById('latency-grid');
  if (!grid) return;

  if (!tools || tools.length === 0) {
    grid.innerHTML = `
      <div class="empty-state" style="padding:24px; grid-column:1/-1;">
        <span class="empty-icon">⏱</span>
        <span class="empty-title">No tool executions yet</span>
        <span class="empty-body">Run an agent with a prompt to see latency data here.</span>
      </div>`;
    return;
  }

  const maxMs = Math.max(...tools.map(t => t.p95_ms || t.avg_ms || 1), 1);

  grid.innerHTML = tools.map(t => {
    const avgPct = Math.min(100, ((t.avg_ms || 0) / maxMs) * 100).toFixed(1);
    const p95Pct = Math.min(100, ((t.p95_ms || 0) / maxMs) * 100).toFixed(1);

    return `
      <div class="latency-card">
        <div class="latency-header">
          <span class="latency-tool-name">${escHtml(t.tool_name)}</span>
          <span class="latency-calls">${t.call_count} calls</span>
        </div>
        <div class="latency-bar-row">
          <span class="latency-bar-label">avg</span>
          <div class="latency-bar-track">
            <div class="latency-bar-fill" style="width:${avgPct}%"></div>
          </div>
          <span class="latency-val">${t.avg_ms != null ? t.avg_ms.toFixed(1) + 'ms' : '—'}</span>
        </div>
        <div class="latency-bar-row">
          <span class="latency-bar-label">p95</span>
          <div class="latency-bar-track">
            <div class="latency-bar-fill p95" style="width:${p95Pct}%"></div>
          </div>
          <span class="latency-val">${t.p95_ms != null ? t.p95_ms.toFixed(1) + 'ms' : '—'}</span>
        </div>
      </div>`;
  }).join('');
}

/* ═══════════════════════════════════════════════════════════════
   5b. OVERVIEW VIOLATIONS PREVIEW
═══════════════════════════════════════════════════════════════ */

function renderOverviewViolations(data) {
  const tbody   = document.getElementById('overview-violations-tbody');
  const counter = document.getElementById('overview-violations-count');
  if (!tbody) return;

  if (counter) counter.textContent = data.total;

  if (!data.violations || data.violations.length === 0) {
    tbody.innerHTML = `
      <tr><td colspan="5">
        <div class="empty-state" style="padding:20px;">
          <span class="empty-icon">✅</span>
          <span class="empty-title">No violations yet</span>
        </div>
      </td></tr>`;
    return;
  }

  tbody.innerHTML = data.violations.slice(0, 5).map(v => {
    const inputStr = v.attempted_input
      ? JSON.stringify(v.attempted_input).slice(0, 50)
      : '—';
    return `
      <tr>
        <td class="td-mono td-dim">${relativeTime(v.timestamp)}</td>
        <td>${escHtml(v.agent_name || '—')}</td>
        <td><span class="badge badge-tool">${escHtml(v.tool_name || '—')}</span></td>
        <td>
          <span class="violation-input"
                style="font-family:var(--font-mono);font-size:11.5px;">
            ${escHtml(inputStr)}
          </span>
        </td>
        <td style="font-size:12px;color:var(--text-secondary);
                   max-width:180px;overflow:hidden;
                   text-overflow:ellipsis;white-space:nowrap;">
          ${escHtml((v.denial_message || '').slice(0, 60))}
        </td>
      </tr>`;
  }).join('');
}

/* ═══════════════════════════════════════════════════════════════
   6. VIOLATIONS TABLE
═══════════════════════════════════════════════════════════════ */

async function refreshViolations() {
  if (state.loading.violations) return;
  state.loading.violations = true;
  setTableLoading('violations-tbody');

  const skip  = state.filters.violationPage * state.filters.pageSize;
  const { ok, data, error } = await fetchViolations(skip, state.filters.pageSize);
  state.loading.violations = false;

  if (!ok) {
    toast(error || 'Failed to load violations', 'error');
    return;
  }

  state.data.violations = data;
  renderViolations(data);
}

function renderViolations(data) {
  const tbody   = document.getElementById('violations-tbody');
  const counter = document.getElementById('violations-count');
  if (counter) counter.textContent = data.total;

  if (!data.violations || data.violations.length === 0) {
    tbody.innerHTML = `
      <tr><td colspan="6">
        <div class="empty-state">
          <span class="empty-icon">✅</span>
          <span class="empty-title">No governance violations</span>
          <span class="empty-body">All tool calls are within permitted bounds. Register an agent with limited tools and run a prompt to see violations here.</span>
        </div>
      </td></tr>`;
    renderPagination('violations-pagination', data, state.filters.violationPage, (p) => {
      state.filters.violationPage = p;
      refreshViolations();
    });
    return;
  }

  tbody.innerHTML = data.violations.map(v => {
    const inputStr = v.attempted_input
      ? JSON.stringify(v.attempted_input)
      : '—';

    return `
      <tr>
        <td class="td-mono td-dim">${relativeTime(v.timestamp)}</td>
        <td>
          <span class="badge badge-violation">⛔ violation</span>
        </td>
        <td>
          <span style="font-weight:500;">${escHtml(v.agent_name || '—')}</span>
          <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(v.agent_id.slice(0,8))}…</span>
        </td>
        <td><span class="badge badge-tool">${escHtml(v.tool_name || '—')}</span></td>
        <td>
          <div class="violation-detail">
            <span class="violation-input">${escHtml(inputStr)}</span>
          </div>
        </td>
        <td>
          <div style="max-width:220px; font-size:12px; color:var(--text-secondary); line-height:1.5;">
            ${escHtml(v.denial_message || '—')}
          </div>
        </td>
      </tr>`;
  }).join('');

  renderPagination('violations-pagination', data, state.filters.violationPage, (p) => {
    state.filters.violationPage = p;
    refreshViolations();
  });
}

/* ═══════════════════════════════════════════════════════════════
   7. AUDIT LOG TABLE
═══════════════════════════════════════════════════════════════ */

async function refreshAuditLog() {
  if (state.loading.audit) return;
  state.loading.audit = true;
  setTableLoading('audit-tbody');

  const skip = state.filters.auditPage * state.filters.pageSize;
  const { ok, data, error } = await fetchAuditLogs(
    skip,
    state.filters.pageSize,
    state.filters.auditEventType,
  );
  state.loading.audit = false;

  if (!ok) {
    toast(error || 'Failed to load audit log', 'error');
    return;
  }

  state.data.audit = data;
  renderAuditLog(data);
}

function renderAuditLog(data) {
  const tbody   = document.getElementById('audit-tbody');
  const counter = document.getElementById('audit-count');
  if (counter) counter.textContent = data.total;

  if (!data.events || data.events.length === 0) {
    tbody.innerHTML = `
      <tr><td colspan="7">
        <div class="empty-state">
          <span class="empty-icon">📋</span>
          <span class="empty-title">No audit events</span>
          <span class="empty-body">Submit a run via POST /agents/run to populate the audit log.</span>
        </div>
      </td></tr>`;
    renderPagination('audit-pagination', data, state.filters.auditPage, (p) => {
      state.filters.auditPage = p;
      refreshAuditLog();
    });
    return;
  }

  tbody.innerHTML = data.events.map(e => {
    const inputSummary = e.input_data
      ? JSON.stringify(e.input_data).slice(0, 60) + (JSON.stringify(e.input_data).length > 60 ? '…' : '')
      : '—';

    const permittedBadge = e.permitted === true
      ? '<span class="badge badge-permitted">✓ allowed</span>'
      : e.permitted === false
        ? '<span class="badge badge-blocked">⛔ blocked</span>'
        : '<span style="color:var(--text-muted);font-size:12px;">—</span>';

    return `
      <tr>
        <td class="td-mono td-dim">${relativeTime(e.timestamp)}</td>
        <td>${eventTypeBadge(e.event_type)}</td>
        <td>
          <span style="font-weight:500;">${escHtml(e.agent_name || '—')}</span>
        </td>
        <td>
          ${e.tool_name
            ? `<span class="badge badge-tool">${escHtml(e.tool_name)}</span>`
            : '<span class="td-dim">—</span>'
          }
        </td>
        <td>${permittedBadge}</td>
        <td>
          <div class="td-truncate td-mono" style="font-size:11.5px; color:var(--text-secondary);">
            ${escHtml(inputSummary)}
          </div>
        </td>
        <td>
          ${e.latency_ms != null
            ? `<span class="td-mono" style="font-size:12px; color:var(--accent-teal);">${e.latency_ms.toFixed(1)}ms</span>`
            : '<span class="td-dim">—</span>'
          }
        </td>
      </tr>`;
  }).join('');

  renderPagination('audit-pagination', data, state.filters.auditPage, (p) => {
    state.filters.auditPage = p;
    refreshAuditLog();
  });
}

/* ═══════════════════════════════════════════════════════════════
   7b. AGENT INTERACTIONS TABLE
═══════════════════════════════════════════════════════════════ */

async function refreshInteractions() {
  if (state.loading.interactions) return;
  state.loading.interactions = true;
  setTableLoading('interactions-tbody');

  const skip  = state.filters.interactionsPage * state.filters.pageSize;
  const { ok, data, error } = await fetchInteractions(skip, state.filters.pageSize);
  state.loading.interactions = false;

  if (!ok) {
    toast(error || 'Failed to load interactions', 'error');
    return;
  }

  state.data.interactions = data;
  renderInteractions(data);
}

function renderInteractions(data) {
  const tbody   = document.getElementById('interactions-tbody');
  const counter = document.getElementById('interactions-count');
  if (counter) counter.textContent = data.total;

  if (!data.interactions || data.interactions.length === 0) {
    tbody.innerHTML = `
      <tr><td colspan="5">
        <div class="empty-state">
          <span class="empty-icon">🔗</span>
          <span class="empty-title">No agent interactions yet</span>
          <span class="empty-body">
            Use POST /agent-interactions to record a handoff, delegation,
            request, or response between two registered agents.
          </span>
        </div>
      </td></tr>`;
    renderPagination('interactions-pagination', data, state.filters.interactionsPage, (p) => {
      state.filters.interactionsPage = p;
      refreshInteractions();
    });
    return;
  }

  const typeBadge = (type) => {
    const map = {
      'handoff':    '<span class="badge badge-handoff">⇒ handoff</span>',
      'delegation': '<span class="badge badge-delegation">↓ delegation</span>',
      'request':    '<span class="badge badge-request">? request</span>',
      'response':   '<span class="badge badge-response">✓ response</span>',
    };
    return map[type] || `<span class="badge">${escHtml(type)}</span>`;
  };

  tbody.innerHTML = data.interactions.map(i => `
    <tr>
      <td class="td-mono td-dim">${relativeTime(i.created_at)}</td>
      <td>${typeBadge(i.interaction_type)}</td>
      <td>
        <span style="font-weight:500;">${escHtml(i.source_agent_name || '—')}</span>
        <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(i.source_agent_id.slice(0,8))}…</span>
      </td>
      <td>
        <span style="font-weight:500;">${escHtml(i.target_agent_name || '—')}</span>
        <br><span class="td-mono td-dim" style="font-size:11px;">${escHtml(i.target_agent_id.slice(0,8))}…</span>
      </td>
      <td>
        <div style="max-width:260px; font-size:12px; color:var(--text-secondary);
                    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
          ${i.message ? escHtml(i.message) : '<span class="td-dim">—</span>'}
        </div>
      </td>
    </tr>`).join('');

  renderPagination('interactions-pagination', data, state.filters.interactionsPage, (p) => {
    state.filters.interactionsPage = p;
    refreshInteractions();
  });
}

/* ═══════════════════════════════════════════════════════════════
   7c. POLICIES TABLE
═══════════════════════════════════════════════════════════════ */

async function refreshPolicies() {
  if (state.loading.policies) return;
  state.loading.policies = true;
  setTableLoading('policies-tbody');
  const skip  = state.filters.policiesPage * state.filters.pageSize;
  const { ok, data, error } = await fetchPolicies(skip, state.filters.pageSize);
  state.loading.policies = false;
  if (!ok) { toast(error || 'Failed to load policies', 'error'); return; }
  state.data.policies = data;
  renderPolicies(data);
}

function renderPolicies(data) {
  const tbody   = document.getElementById('policies-tbody');
  const counter = document.getElementById('policies-count');
  if (!tbody) return;
  if (counter) counter.textContent = data.total;

  if (!data.policies || data.policies.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7"><div class="empty-state">
      <span class="empty-icon">🛡</span>
      <span class="empty-title">No policies defined yet</span>
      <span class="empty-body">Create policies via POST /policies.</span>
    </div></td></tr>`;
    renderPagination('policies-pagination', data, state.filters.policiesPage, (p) => {
      state.filters.policiesPage = p; refreshPolicies();
    });
    return;
  }

  const severityBadge = (sev) => {
    const map = {
      'LOW':      '<span class="badge" style="background:rgba(63,185,80,0.15);color:var(--accent-green);">LOW</span>',
      'MEDIUM':   '<span class="badge" style="background:rgba(227,135,62,0.15);color:var(--accent-orange);">MEDIUM</span>',
      'HIGH':     '<span class="badge" style="background:rgba(248,81,73,0.15);color:var(--accent-red);">HIGH</span>',
      'CRITICAL': '<span class="badge" style="background:rgba(188,140,255,0.15);color:var(--accent-purple);">CRITICAL</span>',
    };
    return map[sev] || `<span class="badge">${escHtml(sev)}</span>`;
  };

  const ruleTypeBadge = (rt) => {
    const labels = {
      'tool_allow':   '✓ tool_allow',
      'tool_deny':    '⛔ tool_deny',
      'rate_limit':   '⏱ rate_limit',
      'prompt_guard': '🔍 prompt_guard',
      'time_window':  '🕐 time_window',
    };
    return `<span class="badge badge-tool">${escHtml(labels[rt] || rt)}</span>`;
  };

  // Refresh the assign dropdown with current policies
  const assignSelect = document.getElementById('assign-policy-select');
  if (assignSelect) {
    const currentVal = assignSelect.value;
    assignSelect.innerHTML = '<option value="">— select a policy —</option>' +
      (data.policies || []).map(p =>
        `<option value="${escHtml(p.id)}">${escHtml(p.name)} (${escHtml(p.rule_type)})</option>`
      ).join('');
    if (currentVal) assignSelect.value = currentVal;
  }

  tbody.innerHTML = data.policies.map(p => `
    <tr>
      <td><span style="font-weight:600;">${escHtml(p.name)}</span>
        ${p.description ? `<br><span class="td-dim" style="font-size:11px;">${escHtml(p.description.slice(0,50))}</span>` : ''}
      </td>
      <td>${ruleTypeBadge(p.rule_type)}</td>
      <td>${severityBadge(p.severity)}</td>
      <td>${p.is_active
          ? '<span class="badge badge-permitted">✓ active</span>'
          : '<span class="badge badge-run-end">○ inactive</span>'}</td>
      <td><span class="td-mono" style="font-size:12px;">${p.agent_count}</span></td>
      <td><div class="td-truncate td-mono" style="font-size:11px;max-width:180px;color:var(--text-secondary);">
        ${escHtml(JSON.stringify(p.rule_config))}
      </div></td>
      <td class="td-mono td-dim">${relativeTime(p.created_at)}</td>
    </tr>`).join('');

  renderPagination('policies-pagination', data, state.filters.policiesPage, (p) => {
    state.filters.policiesPage = p; refreshPolicies();
  });
}

/* ═══════════════════════════════════════════════════════════════
   8. UTILITIES
═══════════════════════════════════════════════════════════════ */

function escHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function relativeTime(isoString) {
  if (!isoString) return '—';
  const date  = new Date(isoString.endsWith('Z') ? isoString : isoString + 'Z');
  const diff  = (Date.now() - date.getTime()) / 1000;
  if (diff < 5)   return 'just now';
  if (diff < 60)  return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
  return date.toLocaleDateString();
}

function eventTypeBadge(type) {
  const map = {
    'run_start': '<span class="badge badge-run-start">▶ run_start</span>',
    'tool_call': '<span class="badge badge-tool-call">⚙ tool_call</span>',
    'tool_end':  '<span class="badge badge-tool-end">✓ tool_end</span>',
    'violation': '<span class="badge badge-violation">⛔ violation</span>',
    'run_end':   '<span class="badge badge-run-end">■ run_end</span>',
    'agent_handoff':    '<span class="badge badge-handoff">⇒ agent_handoff</span>',
    'policy_violation': '<span class="badge badge-violation">🛡 policy_violation</span>',
  };
  return map[type] || `<span class="badge">${escHtml(type)}</span>`;
}

function trustLevelBadge(level) {
  const map = {
    'TRUSTED':   '<span class="badge" style="background:rgba(63,185,80,0.15);color:#3fb950;">🏅 TRUSTED</span>',
    'MONITORED': '<span class="badge" style="background:rgba(79,142,247,0.15);color:#4f8ef7;">👁 MONITORED</span>',
    'WARNING':   '<span class="badge" style="background:rgba(227,135,62,0.15);color:#e3873e;">⚠ WARNING</span>',
    'HIGH_RISK': '<span class="badge" style="background:rgba(248,81,73,0.15);color:#f85149;">🔴 HIGH_RISK</span>',
  };
  return map[level] || `<span class="badge">${escHtml(level)}</span>`;
}

function formatTrustScore(score) {
  if (score === null || score === undefined) return '—';
  const color = score >= 90 ? '#3fb950'
              : score >= 70 ? '#4f8ef7'
              : score >= 50 ? '#e3873e'
              : '#f85149';
  return `<span style="font-family:var(--font-mono);font-weight:700;color:${color};">${score.toFixed(1)}</span>`;
}

function riskLevelBadge(level) {
  const map = {
    'SAFE':     '<span class="badge" style="background:rgba(63,185,80,0.15);color:#3fb950;">✅ SAFE</span>',
    'LOW':      '<span class="badge" style="background:rgba(57,197,207,0.15);color:#39c5cf;">🔵 LOW</span>',
    'MEDIUM':   '<span class="badge" style="background:rgba(227,135,62,0.15);color:#e3873e;">🟡 MEDIUM</span>',
    'HIGH':     '<span class="badge" style="background:rgba(248,81,73,0.15);color:#f85149;">🔴 HIGH</span>',
    'CRITICAL': '<span class="badge" style="background:rgba(248,81,73,0.25);color:#f85149;font-weight:700;">🚨 CRITICAL</span>',
  };
  return map[level] || `<span class="badge">${escHtml(level)}</span>`;
}

function formatRiskScore(score) {
  if (score === null || score === undefined) return '—';
  const color = score >= 75 ? '#f85149'
              : score >= 50 ? '#e3873e'
              : score >= 25 ? '#e3c93e'
              : '#3fb950';
  return `<span style="font-family:var(--font-mono);font-weight:700;color:${color};">${score.toFixed(1)}</span>`;
}

function setTableLoading(tbodyId) {
  const el = document.getElementById(tbodyId);
  if (!el) return;
  el.innerHTML = Array.from({ length: 5 }, () => `
    <tr>${Array.from({ length: 7 }, () =>
      `<td><div class="skeleton" style="height:16px; width:${60 + Math.random()*60 | 0}px;"></div></td>`
    ).join('')}</tr>
  `).join('');
}

function renderPagination(containerId, data, currentPage, onPage) {
  const el = document.getElementById(containerId);
  if (!el) return;

  const totalPages = Math.ceil(data.total / (data.limit || 25));
  if (totalPages <= 1) { el.innerHTML = ''; return; }

  const prevDisabled = currentPage === 0;
  const nextDisabled = currentPage >= totalPages - 1;

  el.innerHTML = `
    <div style="display:flex; align-items:center; gap:10px; justify-content:flex-end; padding:12px 14px; border-top:1px solid var(--border);">
      <span style="font-size:12px; color:var(--text-muted);">
        ${data.skip + 1}–${Math.min(data.skip + data.limit, data.total)} of ${data.total}
      </span>
      <button class="btn btn-ghost btn-sm" ${prevDisabled ? 'disabled' : ''} onclick="(${onPage.toString()})(${currentPage - 1})">
        ← Prev
      </button>
      <button class="btn btn-ghost btn-sm" ${nextDisabled ? 'disabled' : ''} onclick="(${onPage.toString()})(${currentPage + 1})">
        Next →
      </button>
    </div>`;
}

function updateConnectionStatus(status, label) {
  const dot  = document.getElementById('status-dot');
  const text = document.getElementById('status-text');
  if (dot)  { dot.className = `status-dot ${status}`; }
  if (text) { text.textContent = label || status; }
}

function updateLastRefresh() {
  state.lastRefresh = new Date();
  const el = document.getElementById('last-refresh');
  if (el) el.textContent = `Refreshed ${relativeTime(state.lastRefresh.toISOString())}`;
}

let _refreshLabelTimer = null;
function startRefreshLabelUpdater() {
  clearInterval(_refreshLabelTimer);
  _refreshLabelTimer = setInterval(() => {
    if (!state.lastRefresh) return;
    const el = document.getElementById('last-refresh');
    if (el) el.textContent = `Refreshed ${relativeTime(state.lastRefresh.toISOString())}`;
  }, 15_000);
}

/* Toast notifications */
function toast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const icons = { error: '⚠', success: '✓', info: 'ℹ' };
  const el    = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `<span>${icons[type] || icons.info}</span><span>${escHtml(message)}</span>`;
  container.appendChild(el);

  setTimeout(() => {
    el.style.animation = 'toast-out 0.2s ease forwards';
    setTimeout(() => el.remove(), 200);
  }, 3500);
}

/* ═══════════════════════════════════════════════════════════════
   8b. CHARTS (Chart.js — Phase 6)
═══════════════════════════════════════════════════════════════ */

const _charts = {};

function _destroyChart(id) {
  if (_charts[id]) {
    _charts[id].destroy();
    delete _charts[id];
  }
}

const _CHART_DEFAULTS = {
  font: { family: 'Inter, system-ui, sans-serif', size: 12 },
  color: '#8b9ab4',
};

const _TRUST_COLORS = ['#3fb950','#4f8ef7','#e3873e','#f85149'];
const _RISK_COLORS  = ['#3fb950','#39c5cf','#e3c93e','#f85149','#b80000'];
const _INTER_COLORS = ['#4f8ef7','#bc8cff','#e3873e','#39c5cf'];
const _TOOL_COLORS  = ['#4f8ef7','#39c5cf','#3fb950','#bc8cff',
                       '#e3873e','#f85149','#e3c93e','#8b9ab4',
                       '#ff7eb3','#43c4e8'];

function _showChartEmpty(id, show) {
  const empty  = document.getElementById('chart-' + id + '-empty');
  const canvas = document.getElementById('chart-' + id);
  if (!empty || !canvas) return;
  if (show) {
    empty.style.display  = 'flex';
    canvas.style.display = 'none';
  } else {
    empty.style.display  = 'none';
    canvas.style.display = 'block';
  }
}

function renderTrustChart(trustDist) {
  const id = 'trust';
  _destroyChart(id);
  const labels = ['TRUSTED','MONITORED','WARNING','HIGH_RISK'];
  const values = labels.map(l => (trustDist || {})[l] || 0);
  if (values.every(v => v === 0)) { _showChartEmpty(id, true); return; }
  _showChartEmpty(id, false);
  const ctx = document.getElementById('chart-' + id).getContext('2d');
  _charts[id] = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{ data: values, backgroundColor: _TRUST_COLORS,
                   borderColor: '#1c2333', borderWidth: 2, hoverOffset: 6 }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'right',
                  labels: { color: '#8b9ab4', font: _CHART_DEFAULTS.font, boxWidth: 14 } },
        tooltip: { callbacks: { label: c => ' ' + c.label + ': ' + c.raw + (c.raw !== 1 ? ' agents' : ' agent') } },
      },
    },
  });
}

function renderRiskChart(riskDist) {
  const id = 'risk';
  _destroyChart(id);
  const labels = ['SAFE','LOW','MEDIUM','HIGH','CRITICAL'];
  const values = labels.map(l => (riskDist || {})[l] || 0);
  if (values.every(v => v === 0)) { _showChartEmpty(id, true); return; }
  _showChartEmpty(id, false);
  const ctx = document.getElementById('chart-' + id).getContext('2d');
  _charts[id] = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{ data: values, backgroundColor: _RISK_COLORS,
                   borderColor: '#1c2333', borderWidth: 2, hoverOffset: 6 }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'right',
                  labels: { color: '#8b9ab4', font: _CHART_DEFAULTS.font, boxWidth: 14 } },
        tooltip: { callbacks: { label: c => ' ' + c.label + ': ' + c.raw + (c.raw !== 1 ? ' agents' : ' agent') } },
      },
    },
  });
}

function renderInteractionChart(interactionsByType) {
  const id = 'interactions';
  _destroyChart(id);
  const entries = Object.entries(interactionsByType || {}).filter(function(e) { return e[1] > 0; });
  if (entries.length === 0) { _showChartEmpty(id, true); return; }
  _showChartEmpty(id, false);
  const labels = entries.map(function(e) { return e[0]; });
  const values = entries.map(function(e) { return e[1]; });
  const ctx = document.getElementById('chart-' + id).getContext('2d');
  _charts[id] = new Chart(ctx, {
    type: 'pie',
    data: {
      labels,
      datasets: [{ data: values,
                   backgroundColor: _INTER_COLORS.slice(0, labels.length),
                   borderColor: '#1c2333', borderWidth: 2, hoverOffset: 6 }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'right',
                  labels: { color: '#8b9ab4', font: _CHART_DEFAULTS.font, boxWidth: 14 } },
        tooltip: { callbacks: { label: c => ' ' + c.label + ': ' + c.raw } },
      },
    },
  });
}

function renderToolUsageChart(toolLatency) {
  const id = 'tools';
  _destroyChart(id);
  const data = (toolLatency || []).filter(function(t) { return t.call_count > 0; });
  if (data.length === 0) { _showChartEmpty(id, true); return; }
  _showChartEmpty(id, false);
  const labels = data.map(function(t) { return t.tool_name; });
  const values = data.map(function(t) { return t.call_count; });
  const ctx = document.getElementById('chart-' + id).getContext('2d');
  _charts[id] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{ label: 'Tool Calls', data: values,
                   backgroundColor: _TOOL_COLORS.slice(0, labels.length),
                   borderColor: '#1c2333', borderWidth: 1, borderRadius: 4 }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: c => ' ' + c.raw + (c.raw !== 1 ? ' calls' : ' call') } },
      },
      scales: {
        x: { ticks: { color: '#8b9ab4', font: _CHART_DEFAULTS.font },
             grid:  { color: 'rgba(42,52,80,0.5)' } },
        y: { beginAtZero: true,
             ticks: { color: '#8b9ab4', font: _CHART_DEFAULTS.font, precision: 0 },
             grid:  { color: 'rgba(42,52,80,0.5)' } },
      },
    },
  });
}

function renderViolationChart(totalViolations, totalPolicyViolations) {
  const id = 'violations';
  _destroyChart(id);
  const gov = totalViolations || 0;
  const pol = totalPolicyViolations || 0;
  if (gov === 0 && pol === 0) { _showChartEmpty(id, true); return; }
  _showChartEmpty(id, false);
  const ctx = document.getElementById('chart-' + id).getContext('2d');
  _charts[id] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: ['Governance Violations', 'Policy Violations'],
      datasets: [{ label: 'Count', data: [gov, pol],
                   backgroundColor: ['#f85149','#e3873e'],
                   borderColor: '#1c2333', borderWidth: 1,
                   borderRadius: 4, barThickness: 48 }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: c => ' ' + c.raw + (c.raw !== 1 ? ' violations' : ' violation') } },
      },
      scales: {
        x: { ticks: { color: '#8b9ab4', font: _CHART_DEFAULTS.font },
             grid:  { color: 'rgba(42,52,80,0.5)' } },
        y: { beginAtZero: true,
             ticks: { color: '#8b9ab4', font: _CHART_DEFAULTS.font, precision: 0 },
             grid:  { color: 'rgba(42,52,80,0.5)' } },
      },
    },
  });
}

function renderAllCharts(statsData) {
  renderTrustChart(statsData.trust_distribution || {});
  renderRiskChart(statsData.risk_distribution || {});
  renderInteractionChart(statsData.interactions_by_type || {});
  renderToolUsageChart(statsData.tool_latency || []);
  renderViolationChart(
    statsData.total_violations || 0,
    statsData.total_policy_violations || 0
  );
}

/* ═══════════════════════════════════════════════════════════════
   9. AUTO-REFRESH + INIT
═══════════════════════════════════════════════════════════════ */

const REFRESH_INTERVAL_MS = 30_000;

function refreshCurrentView() {
  if (!state.token) return;

  const btn = document.getElementById('btn-refresh');
  if (btn) {
    btn.classList.add('btn-spinning');
    btn.querySelector('.btn-icon').style.animation = 'spin 0.8s linear infinite';
  }

  const views = {
    overview:     refreshOverview,
    violations:   refreshViolations,
    audit:        refreshAuditLog,
    interactions: refreshInteractions,
    policies:     refreshPolicies,
  };
  const fn = views[state.currentView];
  if (fn) fn().finally(() => {
    updateLastRefresh();
    if (btn) btn.querySelector('.btn-icon').style.animation = '';
  });
}

function startAutoRefresh() {
  stopAutoRefresh();
  state.refreshTimer = setInterval(refreshCurrentView, REFRESH_INTERVAL_MS);
}

function stopAutoRefresh() {
  if (state.refreshTimer) clearInterval(state.refreshTimer);
  state.refreshTimer = null;
}

/* ── Boot ─────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {

  // Nav click handlers
  document.querySelectorAll('.nav-item[data-view]').forEach(el => {
    el.addEventListener('click', () => navigateTo(el.dataset.view));
  });

  // Refresh button
  document.getElementById('btn-refresh')?.addEventListener('click', refreshCurrentView);

  // Disconnect button
  document.getElementById('btn-disconnect')?.addEventListener('click', () => {
    clearToken();
    toast('Disconnected', 'info');
  });

  // Register form
  document.getElementById('btn-register')?.addEventListener('click', handleRegister);

  // Paste token
  document.getElementById('btn-use-token')?.addEventListener('click', handlePasteToken);

  // Audit event type filter
  document.getElementById('audit-filter-type')?.addEventListener('change', (e) => {
    state.filters.auditEventType = e.target.value;
    state.filters.auditPage      = 0;
    refreshAuditLog();
  });

  // ── Run Agent panel ───────────────────────────────────────
  document.getElementById('btn-run-agent')?.addEventListener('click', async () => {
    const prompt = document.getElementById('run-prompt')?.value?.trim();
    const statusEl = document.getElementById('run-status');
    const resultEl = document.getElementById('run-result');

    if (!state.token) {
      return toast('Please register or connect an agent first.', 'error');
    }
    if (!prompt) {
      return toast('Please enter a prompt.', 'error');
    }

    const btn = document.getElementById('btn-run-agent');
    btn.disabled = true;
    btn.textContent = '⟳ Running…';
    if (statusEl) statusEl.textContent = 'Sending to agent…';
    if (resultEl) resultEl.style.display = 'none';

    const { ok, data, error } = await apiFetch('/agents/run', {
      method: 'POST',
      body: JSON.stringify({ prompt }),
    });

    btn.disabled = false;
    btn.textContent = '▶ Run Agent';

    if (!ok) {
      if (statusEl) statusEl.textContent = '';
      toast(error || 'Run failed', 'error');
      return;
    }

    const violations = data.violation_count || 0;
    if (statusEl) statusEl.textContent =
      `✓ Completed in ${data.latency_ms ? data.latency_ms.toFixed(0) + 'ms' : '?ms'} · ${violations} violation${violations !== 1 ? 's' : ''}`;

    if (resultEl) {
      resultEl.style.display = 'block';
      resultEl.innerHTML =
        `<strong style="color:var(--text-primary);">Result:</strong><br>${escHtml(data.result || '—')}`;
    }

    // Refresh dashboard stats so charts and cards update immediately
    setTimeout(() => refreshOverview(), 1000);
  });

  // ── Create Policy ────────────────────────────────────────
  document.getElementById('policy-rule-type')?.addEventListener('change', (e) => {
    const hint = _POLICY_CONFIG_HINTS[e.target.value] || '';
    const hintEl = document.getElementById('policy-config-hint');
    const configEl = document.getElementById('policy-config');
    if (hintEl) hintEl.textContent = `example: ${hint}`;
    if (configEl && !configEl.value) configEl.placeholder = hint;
  });

  // Trigger hint on load
  const initialType = document.getElementById('policy-rule-type')?.value;
  if (initialType) {
    const hint = _POLICY_CONFIG_HINTS[initialType] || '';
    const hintEl = document.getElementById('policy-config-hint');
    const configEl = document.getElementById('policy-config');
    if (hintEl) hintEl.textContent = `example: ${hint}`;
    if (configEl) configEl.placeholder = hint;
  }

  document.getElementById('btn-create-policy')?.addEventListener('click', async () => {
    const name        = document.getElementById('policy-name')?.value?.trim();
    const rule_type   = document.getElementById('policy-rule-type')?.value;
    const severity    = document.getElementById('policy-severity')?.value;
    const description = document.getElementById('policy-description')?.value?.trim();
    const configRaw   = document.getElementById('policy-config')?.value?.trim();
    const statusEl    = document.getElementById('policy-create-status');

    if (!state.token) return toast('Connect an agent first.', 'error');
    if (!name)        return toast('Policy name is required.', 'error');
    if (!configRaw)   return toast('Rule config is required.', 'error');

    let rule_config;
    try {
      rule_config = JSON.parse(configRaw);
    } catch {
      return toast('Rule config must be valid JSON.', 'error');
    }

    const btn = document.getElementById('btn-create-policy');
    btn.disabled = true;
    btn.textContent = '⟳ Creating…';
    if (statusEl) statusEl.textContent = '';

    const { ok, data, error } = await createPolicy({
      name, rule_type, severity, description, rule_config, is_active: true,
    });

    btn.disabled = false;
    btn.textContent = '🛡 Create Policy';

    if (!ok) {
      toast(error || 'Failed to create policy', 'error');
      return;
    }

    toast(`Policy "${name}" created!`, 'success');
    if (statusEl) statusEl.textContent = `✓ Created — ID: ${data.id}`;

    // Clear form
    document.getElementById('policy-name').value = '';
    document.getElementById('policy-config').value = '';
    document.getElementById('policy-description').value = '';

    // Refresh policies table and dropdown
    refreshPolicies();
  });

  // ── Assign Policy to Agent ────────────────────────────────
  // Pre-fill agent ID with the currently connected agent
  const assignAgentInput = document.getElementById('assign-agent-id');
  if (assignAgentInput && state.agentId) {
    assignAgentInput.value = state.agentId;
  }

  document.getElementById('btn-assign-policy')?.addEventListener('click', async () => {
    const policyId = document.getElementById('assign-policy-select')?.value;
    const agentId  = document.getElementById('assign-agent-id')?.value?.trim();
    const statusEl = document.getElementById('policy-assign-status');

    if (!state.token) return toast('Connect an agent first.', 'error');
    if (!policyId)    return toast('Select a policy.', 'error');
    if (!agentId)     return toast('Enter an agent ID.', 'error');

    const btn = document.getElementById('btn-assign-policy');
    btn.disabled = true;
    btn.textContent = '⟳ Assigning…';
    if (statusEl) statusEl.textContent = '';

    const { ok, data, error } = await assignPolicyToAgent(policyId, agentId);

    btn.disabled = false;
    btn.textContent = '🔗 Assign to Agent';

    if (!ok) {
      toast(error || 'Failed to assign policy', 'error');
      return;
    }

    toast('Policy assigned to agent!', 'success');
    if (statusEl) statusEl.textContent = '✓ Assigned successfully';
    refreshPolicies();
  });

  // Mobile sidebar toggle
  document.getElementById('mobile-menu-btn')?.addEventListener('click', () => {
    document.querySelector('.sidebar')?.classList.toggle('open');
  });

  // Start relative-time label updater
  startRefreshLabelUpdater();

  // Restore session or show auth banner
  if (!tryRestoreSession()) {
    showAuthBanner();
    updateConnectionStatus('disconnected', 'Not connected');
  }
});
