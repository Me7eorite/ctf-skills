import { api, postJson } from "../api.js";
import { showToast } from "../ui/toast.js";
import {
  escapeHtml,
  categoryLabel,
  categoryTone,
  formatDateTime,
  requestStatusPill,
  statusIndicator,
  softPill,
} from "../ui/format.js";
import { setView } from "../router.js";
import { showRunsForRequest } from "./research-runs.js";

const REQUEST_STATUSES = ["draft", "researching", "researched", "failed"];
const ACTIVE_POLL_MS = 2000;
const SETTLED_POLL_MS = 12000;

const state = {
  requests: null,
  categories: null,
  detail: null,
  detailId: null,
  worker: null,
  flags: {},
  filter: { category: "", status: "" },
  detailPoll: { timer: null, loading: false },
  expandedDesigns: {},
};

async function ensureRequests() {
  if (state.requests !== null) return;
  if (state.flags.requests?.loading) return;
  state.flags.requests = { loading: true, error: null };
  try {
    state.requests = await api(buildRequestsUrl());
    state.flags.requests = { loading: false, error: null };
  } catch (err) {
    state.flags.requests = { loading: false, error: err.message };
  }
  render(state.data);
}

async function ensureCategories() {
  if (state.categories !== null) return;
  try {
    state.categories = await api("/api/research/categories");
  } catch {
    state.categories = [];
  }
  render(state.data);
}

async function ensureWorker() {
  if (state.worker !== null) return;
  if (state.flags.worker?.loading) return;
  state.flags.worker = { loading: true };
  try {
    state.worker = await api("/api/research/worker/status");
  } catch {
    state.worker = { running: false, available: false };
  } finally {
    state.flags.worker = { loading: false };
  }
  render(state.data);
}

function buildRequestsUrl() {
  const p = new URLSearchParams();
  if (state.filter.category) p.set("category", state.filter.category);
  if (state.filter.status) p.set("status", state.filter.status);
  return p.toString() ? `/api/research/requests?${p}` : "/api/research/requests";
}

async function fetchDetail(id) {
  if (state.detail !== null) return;
  state.flags.detail = { loading: true };
  try {
    state.detail = await api(`/api/research/requests/${id}`);
    state.flags.detail = { loading: false };
    render(state.data);
    window.lucide?.createIcons();
  } catch (err) {
    showToast(err.message, true);
    state.flags.detail = { loading: false };
    state.detailId = null;
    state.detail = null;
    render(state.data);
  }
}

function isDetailViewActive() {
  const root = document.querySelector('[data-view="research-requests"]');
  return !!root?.classList.contains("active") && !!state.detailId;
}

function detailNeedsActivePolling() {
  const latestStatus = state.detail?.latest_run?.status;
  const requestStatus = state.detail?.request?.status;
  const hasRunningDesign = (state.detail?.design_tasks || []).some((task) =>
    (task.attempts || []).some((attempt) => attempt.status === "running")
  );
  return (
    state.worker?.running ||
    hasRunningDesign ||
    latestStatus === "queued" ||
    latestStatus === "running" ||
    requestStatus === "researching"
  );
}

function clearDetailPoll() {
  if (state.detailPoll.timer) {
    window.clearTimeout(state.detailPoll.timer);
    state.detailPoll.timer = null;
  }
  state.detailPoll.loading = false;
}

function scheduleDetailPoll(delay = ACTIVE_POLL_MS) {
  if (!isDetailViewActive()) {
    clearDetailPoll();
    return;
  }
  if (document.hidden) return;
  if (state.detailPoll.timer) window.clearTimeout(state.detailPoll.timer);
  state.detailPoll.timer = window.setTimeout(() => {
    pollDetail();
  }, delay);
}

async function pollDetail() {
  if (!isDetailViewActive()) {
    clearDetailPoll();
    return;
  }
  if (document.hidden) {
    state.detailPoll.timer = null;
    return;
  }
  if (state.detailPoll.loading) return;

  state.detailPoll.timer = null;
  state.detailPoll.loading = true;
  try {
    const detailId = state.detailId;
    const [detail, worker] = await Promise.all([
      api(`/api/research/requests/${detailId}`),
      api("/api/research/worker/status").catch(() => ({ running: false, available: false })),
    ]);
    if (state.detailId !== detailId) return;
    state.detail = detail;
    state.worker = worker;
    state.requests = null;
    render(state.data);
    window.lucide?.createIcons();
  } catch (err) {
    showToast(err.message, true);
  } finally {
    state.detailPoll.loading = false;
    const delay = detailNeedsActivePolling() ? ACTIVE_POLL_MS : SETTLED_POLL_MS;
    scheduleDetailPoll(delay);
  }
}

async function forceReloadRequests() {
  state.requests = null;
  state.flags.requests = { loading: false, error: null };
  await ensureRequests();
}

async function reloadDetail() {
  state.detail = null;
  if (state.detailId) await fetchDetail(state.detailId);
}

async function refreshDetail({ startPolling = false } = {}) {
  state.worker = null;
  state.requests = null;
  await reloadDetail();
  await ensureRequests();
  await ensureWorker();
  if (startPolling || detailNeedsActivePolling()) scheduleDetailPoll(ACTIVE_POLL_MS);
}

async function generateDesignTasks() {
  if (!state.detailId) return;
  try {
    await postJson(`/api/research/requests/${state.detailId}/design-tasks/generate`, {});
    showToast("Design tasks generated");
    await reloadDetail();
    window.lucide?.createIcons();
  } catch (err) {
    showToast(err.message, true);
  }
}

async function transitionDesignTask(taskId, action) {
  try {
    await postJson(`/api/design-tasks/${taskId}/${action}`, {});
    showToast(`Task ${action}d`);
    await reloadDetail();
    window.lucide?.createIcons();
  } catch (err) {
    showToast(err.message, true);
  }
}

async function designTaskNow(taskId) {
  if (!taskId) return;
  state.flags.designing = { ...(state.flags.designing || {}), [taskId]: true };
  render(state.data);
  window.lucide?.createIcons();
  try {
    const result = await postJson(`/api/design-tasks/${taskId}/design`, {});
    const failed = result.attempt_status === "failed";
    showToast(result.error || (failed ? "Design attempt failed" : "Design completed"), failed);
    state.expandedDesigns[taskId] = true;
    await refreshDetail({ startPolling: true });
    window.lucide?.createIcons();
  } catch (err) {
    showToast(err.message, true);
  } finally {
    state.flags.designing = { ...(state.flags.designing || {}), [taskId]: false };
    render(state.data);
    window.lucide?.createIcons();
  }
}

async function runWorkerAction(action, body = {}) {
  try {
    const result = await postJson(`/api/research/worker/${action}`, body);
    showToast(result.message || "OK");
    state.worker = result.state || null;
    await refreshDetail({ startPolling: true });
  } catch (err) {
    showToast(err.message, true);
  }
}

export function render(data) {
  state.data = data;
  if (!state.detailId) clearDetailPoll();
  ensureRequests();
  ensureCategories();
  const root = document.querySelector('[data-view="research-requests"]');
  if (!root) {
    clearDetailPoll();
    return;
  }

  const flag = state.flags.requests || {};
  if (flag.loading && !state.requests) {
    root.innerHTML = `<div class="empty">Loading requests...</div>`;
    return;
  }
  if (flag.error) {
    root.innerHTML = `
      <div style="border-radius: var(--radius-md); border: 1px solid var(--accent-red-border); background: var(--accent-red-light); padding: var(--space-md);">
        <div style="font-weight: 500;">Load failed</div>
        <p style="font-size: var(--font-md);">${escapeHtml(flag.error)}</p>
      </div>
    `;
    return;
  }

  if (state.detailId) {
    renderDetail(root);
    return;
  }

  const items = state.requests || [];
  const cats = state.categories || [];

  root.innerHTML = `
    <section class="card">
      <div class="card-header">
        <div>
          <div class="card-title">Requests</div>
          <div class="card-subtitle">Open a request to inspect runs, sources, findings, and worker controls.</div>
        </div>
        <span class="pill">${items.length} rows</span>
      </div>
      <div class="filter-bar">
        <label class="filter-item">Category
          <select id="req-filter-cat" class="filter-select">
            <option value=""${state.filter.category === "" ? " selected" : ""}>All</option>
            ${cats.map(c => `<option value="${escapeHtml(c.code)}"${state.filter.category === c.code ? " selected" : ""}>${escapeHtml(c.code)}</option>`).join("")}
          </select>
        </label>
        <label class="filter-item">Status
          <select id="req-filter-status" class="filter-select">
            <option value=""${state.filter.status === "" ? " selected" : ""}>All</option>
            ${REQUEST_STATUSES.map(s => `<option value="${escapeHtml(s)}"${state.filter.status === s ? " selected" : ""}>${escapeHtml(s)}</option>`).join("")}
          </select>
        </label>
        <button id="req-clear-filter" class="filter-clear">Clear</button>
      </div>
      ${items.length ? renderRequestsTable(items) : `<div class="empty card-body">No matching requests</div>`}
    </section>
  `;
}

function renderRequestsTable(items) {
  return `
    <div class="table-container">
      <table class="table">
        <thead>
          <tr>
            <th>#</th>
            <th>Category</th>
            <th>Topic</th>
            <th>Target</th>
            <th>Status</th>
            <th>Created</th>
          </tr>
        </thead>
        <tbody>
          ${items.map(r => `
            <tr class="table-row-clickable" data-id="${escapeHtml(r.id)}">
              <td class="table-cell-id">${items.indexOf(r) + 1}</td>
              <td>${softPill(categoryLabel(r.category), categoryTone(r.category))}</td>
              <td><div class="truncate" style="max-width: 360px;">${escapeHtml(r.topic)}</div></td>
              <td style="text-align: right;">${r.target_count}</td>
              <td>${requestStatusPill(r.status)}</td>
              <td class="table-cell-time">${escapeHtml(formatDateTime(r.created_at))}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderDetail(root) {
  if (state.detail === null) {
    fetchDetail(state.detailId);
    ensureWorker();
    root.innerHTML = `<div class="empty">Loading detail...</div>`;
    return;
  }

  const { request, latest_run: latest, runs = [], sources = [], findings_by_kind = {}, design_tasks = [] } = state.detail;
  const worker = state.worker || {};
  const workerRunning = !!worker.running;
  const available = worker.available !== false;

  root.innerHTML = `
    <div style="display: flex; align-items: center; justify-content: space-between; gap: var(--space-md); flex-wrap: wrap; margin-bottom: var(--space-md);">
      <button class="btn btn-ghost" id="research-back">
        <i data-lucide="arrow-left"></i> Back
      </button>
      <div class="btn-group" style="flex-wrap: wrap;">
        <button class="btn btn-primary btn-sm" id="detail-run-once"${workerRunning || !available ? " disabled" : ""}>
          <i data-lucide="play"></i> Run once
        </button>
        <button class="btn btn-secondary btn-sm" id="detail-run-loop"${workerRunning || !available ? " disabled" : ""}>
          <i data-lucide="rotate-cw"></i> Continue
        </button>
        <button class="btn btn-danger btn-sm" id="detail-run-stop"${!workerRunning || !available ? " disabled" : ""}>
          <i data-lucide="pause"></i> Pause
        </button>
        <button class="btn btn-secondary btn-sm" id="detail-refresh"${state.flags.detail?.refreshing ? " disabled" : ""}>
          <i data-lucide="refresh-cw"></i> Refresh
        </button>
        <button class="btn btn-secondary btn-sm" id="detail-open-runs">
          <i data-lucide="list"></i> Runs
        </button>
        <button class="btn btn-ghost btn-sm" id="detail-open-logs">
          <i data-lucide="file-text"></i> Logs
        </button>
      </div>
    </div>

    <section class="card card-body">
      <div class="flex items-center gap-2" style="flex-wrap: wrap;">
        ${softPill(categoryLabel(request.category), categoryTone(request.category))}
        ${requestStatusPill(request.status)}
        ${statusIndicator(workerRunning ? "running" : "idle")}
      </div>
      <h2 style="font-size: var(--font-lg); font-weight: 600; margin-top: var(--space-sm);">${escapeHtml(request.topic)}</h2>
      <div class="mono" style="font-size: var(--font-sm); color: var(--ink-500); margin-top: 2px;">${escapeHtml(request.id)}</div>
      <dl style="margin-top: var(--space-lg); display: grid; gap: var(--space-md); grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));">
        <div>
          <dt style="font-size: var(--font-xs); color: var(--ink-400); text-transform: uppercase;">target_count</dt>
          <dd style="font-size: var(--font-md); color: var(--ink-700);">${request.target_count}</dd>
        </div>
        <div>
          <dt style="font-size: var(--font-xs); color: var(--ink-400); text-transform: uppercase;">difficulty</dt>
          <dd style="font-size: var(--font-md); color: var(--ink-700);">${Object.entries(request.difficulty_distribution || {}).map(([k, v]) => `${escapeHtml(k)}=${escapeHtml(v)}`).join(", ") || "-"}</dd>
        </div>
        <div>
          <dt style="font-size: var(--font-xs); color: var(--ink-400); text-transform: uppercase;">latest_run</dt>
          <dd style="font-size: var(--font-md); color: var(--ink-700);">${latest ? `${escapeHtml(latest.status)} / attempt ${latest.attempt}` : "-"}</dd>
        </div>
        <div>
          <dt style="font-size: var(--font-xs); color: var(--ink-400); text-transform: uppercase;">worker</dt>
          <dd style="font-size: var(--font-md); color: var(--ink-700);">${escapeHtml(worker.message || "-")}</dd>
        </div>
      </dl>
    </section>

    <section class="card" style="margin-top: var(--space-lg);">
      <div class="card-header">
        <div><div class="card-title">Runs</div></div>
        <span class="pill">${runs.length}</span>
      </div>
      ${runs.length ? renderRunsTable(runs) : `<div class="empty card-body">No runs yet</div>`}
    </section>

    ${renderDesignTasks(design_tasks, request)}
    ${renderFindings(findings_by_kind)}
    ${renderSources(sources)}
  `;

  scheduleDetailPoll(detailNeedsActivePolling() ? ACTIVE_POLL_MS : SETTLED_POLL_MS);
}

function renderRunsTable(runs) {
  return `
    <div class="table-container">
      <table class="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Status</th>
            <th>Worker</th>
            <th>Started</th>
            <th>Finished</th>
          </tr>
        </thead>
        <tbody>
          ${runs.map(run => `
            <tr>
              <td class="table-cell-id">${runs.indexOf(run) + 1}</td>
              <td>${statusIndicator(run.status)}</td>
              <td class="mono" style="font-size: var(--font-mono);">${escapeHtml(run.claimed_by || "-")}</td>
              <td class="table-cell-time">${escapeHtml(formatDateTime(run.started_at))}</td>
              <td class="table-cell-time">${escapeHtml(formatDateTime(run.finished_at))}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderDesignTasks(designTasks, request) {
  const tasks = designTasks || [];
  const counts = tasks.reduce((acc, task) => {
    acc[task.status] = (acc[task.status] || 0) + 1;
    return acc;
  }, {});
  const summaryEntries = Object.entries(counts)
    .map(([status, n]) => `${escapeHtml(status)}=${n}`)
    .join(", ");
  const canGenerate = (request?.status === "researched");
  return `
    <section class="card" style="margin-top: var(--space-lg);">
      <div class="card-header">
        <div>
          <div class="card-title">Design Tasks</div>
          <div class="card-subtitle">${summaryEntries || "No tasks yet"}</div>
        </div>
        <div class="btn-group">
          <button class="btn btn-primary btn-sm" id="design-tasks-generate"${canGenerate ? "" : " disabled"}>
            <i data-lucide="wand"></i> Generate design tasks
          </button>
          <span class="pill">${tasks.length}</span>
        </div>
      </div>
      ${tasks.length ? renderDesignTasksTable(tasks) : `<div class="empty card-body">No design tasks yet — run "Generate design tasks" once the research run completes.</div>`}
    </section>
  `;
}

function renderDesignTasksTable(tasks) {
  return `
    <div class="table-container">
      <table class="table">
        <thead>
          <tr>
            <th>#</th>
            <th>Challenge ID</th>
            <th>Title</th>
            <th>Difficulty</th>
            <th>Primary technique</th>
            <th>Evidence</th>
            <th>Status</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          ${tasks.map(task => `
            <tr data-design-task-id="${escapeHtml(task.id)}">
              <td class="table-cell-id">${task.task_no}</td>
              <td class="mono">${escapeHtml(task.challenge_id)}</td>
              <td><div class="truncate" style="max-width: 280px;">${escapeHtml(task.title)}</div></td>
              <td>${escapeHtml(task.difficulty)}</td>
              <td><div class="truncate" style="max-width: 220px;">${escapeHtml(task.primary_technique)}</div></td>
              <td style="text-align: right;">${(task.finding_ids || []).length}</td>
              <td>${escapeHtml(task.status)}</td>
              <td>
                <div class="btn-group design-task-actions">
                  <button class="btn btn-secondary btn-sm design-task-toggle" title="Design details">
                    <i data-lucide="${state.expandedDesigns[task.id] === false ? "chevron-right" : "chevron-down"}"></i>
                  </button>
                  <button class="btn btn-secondary btn-sm design-task-queue"${task.status === "draft" ? "" : " disabled"}>Queue</button>
                  <button class="btn btn-ghost btn-sm design-task-archive"${(task.status === "draft" || task.status === "queued") ? "" : " disabled"}>Archive</button>
                </div>
              </td>
            </tr>
            ${state.expandedDesigns[task.id] === false ? "" : `
              <tr class="design-panel-row" data-design-task-panel="${escapeHtml(task.id)}">
                <td colspan="8">${renderDesignPanel(task)}</td>
              </tr>
            `}
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderDesignPanel(task) {
  const attempts = task.attempts || [];
  const latestAttempt = attempts.length ? attempts[attempts.length - 1] : null;
  const latestDesign = task.latest_design || null;
  const isDesigning = !!state.flags.designing?.[task.id];
  return `
    <div class="design-panel">
      <div class="design-panel-header">
        <div class="design-panel-title">
          <span class="design-panel-label">Designs</span>
          ${latestAttempt ? statusIndicator(latestAttempt.status) : softPill("No attempts")}
          ${latestDesign ? qualityGatePill(latestDesign.quality_gate_passed) : ""}
        </div>
        <div class="btn-group design-panel-controls">
          <button class="btn btn-primary btn-sm design-task-run${isDesigning ? " btn-loading" : ""}"${task.status === "queued" && !isDesigning ? "" : " disabled"}>
            <i data-lucide="sparkles"></i> Design now
          </button>
          ${latestAttempt?.prompt_artifact_url ? `
            <a class="btn btn-secondary btn-sm" href="${escapeHtml(latestAttempt.prompt_artifact_url)}" target="_blank" rel="noopener">
              <i data-lucide="file-text"></i> View prompt
            </a>
          ` : ""}
          ${latestAttempt?.log_artifact_url ? `
            <a class="btn btn-secondary btn-sm" href="${escapeHtml(latestAttempt.log_artifact_url)}" target="_blank" rel="noopener">
              <i data-lucide="terminal"></i> Hermes log
            </a>
          ` : ""}
        </div>
      </div>
      <div class="design-panel-grid">
        <div class="design-panel-section">
          <div class="design-panel-section-title">Attempts</div>
          ${attempts.length ? renderDesignAttempts(attempts) : `<div class="empty design-empty">No design attempts yet</div>`}
        </div>
        <div class="design-panel-section">
          <div class="design-panel-section-title">Latest Design</div>
          ${latestDesign ? renderLatestDesign(latestDesign) : `<div class="empty design-empty">No draft design yet</div>`}
        </div>
      </div>
    </div>
  `;
}

function renderDesignAttempts(attempts) {
  return `
    <div class="design-attempts">
      ${attempts.map((attempt) => `
        <div class="design-attempt-row">
          <div class="design-attempt-index">#${attempt.attempt}</div>
          <div class="design-attempt-main">
            <div class="design-attempt-top">
              ${statusIndicator(attempt.status)}
              <span class="design-time">${escapeHtml(formatDateTime(attempt.started_at))}</span>
              <span class="design-time">${escapeHtml(formatDateTime(attempt.finished_at))}</span>
            </div>
            ${attempt.last_error ? `<div class="design-error">${escapeHtml(attempt.last_error)}</div>` : ""}
          </div>
          <div class="design-attempt-links">
            ${attempt.prompt_artifact_url ? `<a href="${escapeHtml(attempt.prompt_artifact_url)}" target="_blank" rel="noopener">Prompt</a>` : ""}
            ${attempt.log_artifact_url ? `<a href="${escapeHtml(attempt.log_artifact_url)}" target="_blank" rel="noopener">Log</a>` : ""}
          </div>
        </div>
      `).join("")}
    </div>
  `;
}

function renderLatestDesign(design) {
  return `
    <div class="design-summary">
      <div class="design-summary-line">
        <span>${escapeHtml(design.summary || "-")}</span>
      </div>
      <div class="design-summary-meta">
        <span>${escapeHtml(design.flag_format || "-")}</span>
        ${qualityGatePill(design.quality_gate_passed)}
      </div>
      <details class="design-json" open>
        <summary>Payload</summary>
        ${renderJsonTree(design.payload)}
      </details>
    </div>
  `;
}

function renderJsonTree(value) {
  if (Array.isArray(value)) {
    return `<ol class="json-tree json-list">${value.map((item) => `<li>${renderJsonTree(item)}</li>`).join("")}</ol>`;
  }
  if (value && typeof value === "object") {
    return `
      <dl class="json-tree">
        ${Object.entries(value).map(([key, item]) => `
          <div class="json-pair">
            <dt>${escapeHtml(key)}</dt>
            <dd>${renderJsonTree(item)}</dd>
          </div>
        `).join("")}
      </dl>
    `;
  }
  return `<span class="json-value">${escapeHtml(JSON.stringify(value))}</span>`;
}

function qualityGatePill(passed) {
  return softPill(passed ? "Quality passed" : "Quality failed", passed ? "text-emerald-700 bg-emerald-50" : "text-rose-700 bg-rose-50");
}

function renderFindings(findingsByKind) {
  const entries = Object.entries(findingsByKind || {});
  if (!entries.length) return "";
  const count = entries.reduce((sum, [, items]) => sum + items.length, 0);
  return `
    <section class="card" style="margin-top: var(--space-lg);">
      <div class="card-header">
        <div><div class="card-title">Findings</div></div>
        <span class="pill">${count}</span>
      </div>
      <div style="border-top: 1px solid var(--line);">
        ${entries.map(([kind, items]) => `
          <div style="padding: var(--space-md);">
            <div style="font-size: var(--font-sm); font-weight: 600; color: var(--ink-700); margin-bottom: var(--space-sm);">${escapeHtml(kind)}</div>
            <div style="display: grid; gap: var(--space-sm);">
              ${items.map((item) => `
                <div style="border: 1px solid var(--line); border-radius: var(--radius-md); padding: var(--space-md);">
                  <div style="font-weight: 500; color: var(--ink-800);">${escapeHtml(item.label)}</div>
                  <div style="font-size: var(--font-sm); color: var(--ink-600); margin-top: 2px;">${escapeHtml(item.summary)}</div>
                </div>
              `).join("")}
            </div>
          </div>
        `).join("")}
      </div>
    </section>
  `;
}

function renderSources(sources) {
  if (!sources.length) return "";
  return `
    <section class="card" style="margin-top: var(--space-lg);">
      <div class="card-header">
        <div><div class="card-title">Sources</div></div>
        <span class="pill">${sources.length}</span>
      </div>
      <div style="border-top: 1px solid var(--line);">
        ${sources.map(s => `
          <div style="padding: var(--space-md);">
            <a href="${escapeHtml(s.url)}" target="_blank" style="font-size: var(--font-md); font-weight: 500; color: var(--brand-600);">${escapeHtml(s.title || s.url)}</a>
            <p style="font-size: var(--font-md); color: var(--ink-600); margin-top: 2px;">${escapeHtml(s.summary || "")}</p>
          </div>
        `).join("")}
      </div>
    </section>
  `;
}

export function bind() {
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && isDetailViewActive()) {
      scheduleDetailPoll(ACTIVE_POLL_MS);
    }
  });

  document.addEventListener("click", (e) => {
    const root = document.querySelector('[data-view="research-requests"]');
    if (!root || !root.contains(e.target)) return;

    if (e.target.closest("#research-back")) {
      state.detailId = null;
      state.detail = null;
      clearDetailPoll();
      render(state.data);
      window.lucide?.createIcons();
      return;
    }
    if (e.target.closest("#detail-run-once")) {
      runWorkerAction("start", { kind: "once", max_jobs: 1 });
      return;
    }
    if (e.target.closest("#detail-run-loop")) {
      runWorkerAction("start", { kind: "loop", max_jobs: 1 });
      return;
    }
    if (e.target.closest("#detail-run-stop")) {
      runWorkerAction("stop");
      return;
    }
    if (e.target.closest("#detail-refresh")) {
      state.flags.detail = { ...(state.flags.detail || {}), refreshing: true };
      render(state.data);
      refreshDetail({ startPolling: true }).finally(() => {
        state.flags.detail = { ...(state.flags.detail || {}), refreshing: false };
        render(state.data);
        window.lucide?.createIcons();
      });
      return;
    }
    if (e.target.closest("#detail-open-runs")) {
      showRunsForRequest(state.detailId);
      return;
    }
    if (e.target.closest("#detail-open-logs") || e.target.closest(".detail-open-logs")) {
      setView("research-logs");
      return;
    }
    if (e.target.closest("#req-clear-filter")) {
      state.filter = { category: "", status: "" };
      forceReloadRequests();
      return;
    }
    if (e.target.closest("#design-tasks-generate")) {
      generateDesignTasks();
      return;
    }
    const toggleBtn = e.target.closest(".design-task-toggle");
    if (toggleBtn) {
      const row = toggleBtn.closest("[data-design-task-id]");
      if (row) {
        const taskId = row.dataset.designTaskId;
        state.expandedDesigns[taskId] = state.expandedDesigns[taskId] === false;
        render(state.data);
        window.lucide?.createIcons();
      }
      return;
    }
    const designNowBtn = e.target.closest(".design-task-run");
    if (designNowBtn) {
      const row = designNowBtn.closest("[data-design-task-panel]") ||
        designNowBtn.closest("[data-design-task-id]");
      const taskId = row?.dataset.designTaskPanel || row?.dataset.designTaskId;
      if (taskId) designTaskNow(taskId);
      return;
    }
    const queueBtn = e.target.closest(".design-task-queue");
    if (queueBtn) {
      const row = queueBtn.closest("[data-design-task-id]");
      if (row) transitionDesignTask(row.dataset.designTaskId, "queue");
      return;
    }
    const archiveBtn = e.target.closest(".design-task-archive");
    if (archiveBtn) {
      const row = archiveBtn.closest("[data-design-task-id]");
      if (row) transitionDesignTask(row.dataset.designTaskId, "archive");
      return;
    }

    const row = e.target.closest(".table-row-clickable");
    if (row) {
      state.detailId = row.dataset.id;
      state.detail = null;
      render(state.data);
      window.lucide?.createIcons();
    }
  });

  document.addEventListener("change", (e) => {
    const root = document.querySelector('[data-view="research-requests"]');
    if (!root || !root.contains(e.target)) return;

    if (e.target.id === "req-filter-cat") {
      state.filter.category = e.target.value;
      forceReloadRequests();
    } else if (e.target.id === "req-filter-status") {
      state.filter.status = e.target.value;
      forceReloadRequests();
    }
  });
}
