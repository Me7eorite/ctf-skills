import { api, postJson } from "../api.js";
import { showToast } from "../ui/toast.js";
import { escapeHtml, categoryLabel, categoryTone, statusIndicator, softPill } from "../ui/format.js";
import { setView } from "../router.js";
import { showRunsForRequest } from "./research-runs.js";

const REQUEST_STATUSES = ["draft", "researching", "researched", "failed"];

const state = {
  requests: null,
  categories: null,
  detail: null,
  detailId: null,
  worker: null,
  flags: {},
  filter: { category: "", status: "" },
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

async function forceReloadRequests() {
  state.requests = null;
  state.flags.requests = { loading: false, error: null };
  await ensureRequests();
}

async function reloadDetail() {
  state.detail = null;
  if (state.detailId) await fetchDetail(state.detailId);
}

async function runWorkerAction(action, body = {}) {
  try {
    const result = await postJson(`/api/research/worker/${action}`, body);
    showToast(result.message || "OK");
    state.worker = result.state || null;
    await reloadDetail();
    await ensureWorker();
  } catch (err) {
    showToast(err.message, true);
  }
}

export function render(data) {
  state.data = data;
  ensureRequests();
  ensureCategories();
  const root = document.querySelector('[data-view="research-requests"]');
  if (!root) return;

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
            <th>ID</th>
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
              <td class="table-cell-id" title="${escapeHtml(r.id)}">${escapeHtml(r.id.slice(0, 8))}...</td>
              <td>${softPill(categoryLabel(r.category), categoryTone(r.category))}</td>
              <td><div class="truncate" style="max-width: 360px;">${escapeHtml(r.topic)}</div></td>
              <td style="text-align: right;">${r.target_count}</td>
              <td>${statusIndicator(r.status)}</td>
              <td class="table-cell-time">${escapeHtml(r.created_at?.slice(0, 19) ?? "-")}</td>
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

  const { request, latest_run: latest, runs = [], sources = [], findings_by_kind = {} } = state.detail;
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
        ${statusIndicator(request.status)}
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

    ${renderFindings(findings_by_kind)}
    ${renderSources(sources)}
  `;
}

function renderRunsTable(runs) {
  return `
    <div class="table-container">
      <table class="table">
        <thead>
          <tr>
            <th>Attempt</th>
            <th>Status</th>
            <th>Worker</th>
            <th>Finished</th>
            <th>Log</th>
          </tr>
        </thead>
        <tbody>
          ${runs.map(run => `
            <tr>
              <td style="text-align: center;">${run.attempt}</td>
              <td>${statusIndicator(run.status)}</td>
              <td class="mono" style="font-size: var(--font-mono);">${escapeHtml(run.claimed_by || "-")}</td>
              <td class="table-cell-time">${escapeHtml(run.finished_at?.slice(0, 19) || "-")}</td>
              <td>${run.hermes_log_path ? `<button class="btn btn-ghost btn-sm detail-open-logs"><i data-lucide="file-text"></i></button>` : "-"}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
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
  document.addEventListener("click", (e) => {
    const root = document.querySelector('[data-view="research-requests"]');
    if (!root || !root.contains(e.target)) return;

    if (e.target.closest("#research-back")) {
      state.detailId = null;
      state.detail = null;
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
