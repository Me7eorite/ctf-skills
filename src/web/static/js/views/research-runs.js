import { api } from "../api.js";
import { escapeHtml, categoryLabel, categoryTone, formatDateTime, statusIndicator, softPill } from "../ui/format.js";
import { setView } from "../router.js";

const RUN_STATUSES = ["queued", "running", "completed", "failed"];

const state = {
  runs: null,
  flags: {},
  filter: { status: "", claimed_by: "", generation_request_id: "" },
};

async function ensureRuns() {
  if (state.runs !== null) return;
  if (state.flags.runs?.loading) return;
  state.flags.runs = { loading: true, error: null };
  try {
    const url = buildRunsUrl();
    state.runs = await api(url);
    state.flags.runs = { loading: false, error: null };
  } catch (err) {
    state.flags.runs = { loading: false, error: err.message };
  }
  render(state.data);
}

function buildRunsUrl() {
  const p = new URLSearchParams();
  if (state.filter.status) p.set("status", state.filter.status);
  if (state.filter.claimed_by) p.set("claimed_by", state.filter.claimed_by);
  if (state.filter.generation_request_id) p.set("generation_request_id", state.filter.generation_request_id);
  return p.toString() ? `/api/research/runs?${p}` : "/api/research/runs";
}

async function forceReloadRuns() {
  state.runs = null;
  state.flags.runs = { loading: false, error: null };
  await ensureRuns();
}

export function showRunsForRequest(requestId) {
  state.filter.generation_request_id = requestId || "";
  state.runs = null;
  setView("research-runs");
  ensureRuns();
}

export function render(data) {
  state.data = data;
  ensureRuns();
  const root = document.querySelector('[data-view="research-runs"]');
  if (!root) return;

  const flag = state.flags.runs || {};
  const items = state.runs || [];

  root.innerHTML = `
    <section class="card">
      <div class="card-header">
        <div>
          <div class="card-title">Research runs</div>
          <div class="card-subtitle">Use this page to observe run status, profile, logs, and worker ownership.</div>
        </div>
        <span class="pill">${items.length} rows</span>
      </div>
      ${state.filter.generation_request_id ? `
        <div style="padding: var(--space-md); border-top: 1px solid var(--line); background: var(--ink-50); display: flex; align-items: center; justify-content: space-between; gap: var(--space-md);">
          <span class="mono" style="font-size: var(--font-sm);">request=${escapeHtml(state.filter.generation_request_id)}</span>
          <button id="run-clear-request-filter" class="btn btn-secondary btn-sm">Show all</button>
        </div>
      ` : ""}
      <div class="filter-bar">
        <label class="filter-item">Status
          <select id="run-filter-status" class="filter-select">
            <option value=""${state.filter.status === "" ? " selected" : ""}>All</option>
            ${RUN_STATUSES.map(s => `<option value="${escapeHtml(s)}"${state.filter.status === s ? " selected" : ""}>${escapeHtml(s)}</option>`).join("")}
          </select>
        </label>
        <label class="filter-item">claimed_by
          <input id="run-filter-claimed-by" value="${escapeHtml(state.filter.claimed_by)}" class="filter-input" placeholder="worker id" style="width: 176px;" />
        </label>
        <button id="run-clear-filter" class="filter-clear">Clear</button>
      </div>
      ${flag.loading && !state.runs ? `<div class="empty card-body">Loading runs...</div>` : ""}
      ${flag.error ? renderError(flag.error) : ""}
      ${!flag.loading && !flag.error ? (items.length ? renderRunsTable(items) : `<div class="empty card-body">No matching runs</div>`) : ""}
    </section>
  `;
}

function renderError(message) {
  return `
    <div style="border-top: 1px solid var(--line); padding: var(--space-md); color: var(--accent-red);">
      ${escapeHtml(message)}
    </div>
  `;
}

function renderRunsTable(items) {
  return `
    <div class="table-container">
      <table class="table">
        <thead>
          <tr>
            <th>Run ID</th>
            <th>Category</th>
            <th>Attempt</th>
            <th>Status</th>
            <th>Worker</th>
            <th>Profile</th>
            <th>Started</th>
            <th>Log</th>
          </tr>
        </thead>
        <tbody>
          ${items.map(r => `
            <tr>
              <td class="table-cell-id" title="${escapeHtml(r.id)}">${escapeHtml(r.id.slice(0, 6))}</td>
              <td>${r.category ? softPill(categoryLabel(r.category), categoryTone(r.category)) : "-"}</td>
              <td style="text-align: center;">${r.attempt}</td>
              <td>${statusIndicator(r.status)}</td>
              <td class="mono" style="font-size: var(--font-mono);">${escapeHtml(r.claimed_by || "-")}</td>
              <td class="mono" style="font-size: var(--font-mono);">${escapeHtml(r.profile_name_used || "-")}</td>
              <td class="table-cell-time">${escapeHtml(formatDateTime(r.started_at))}</td>
              <td>
                ${r.hermes_log_path ? `<button class="btn btn-ghost btn-sm run-open-log" data-log="${escapeHtml(r.hermes_log_path)}"><i data-lucide="file-text"></i></button>` : "-"}
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

export function bind() {
  document.addEventListener("click", (e) => {
    const root = document.querySelector('[data-view="research-runs"]');
    if (!root || !root.contains(e.target)) return;

    if (e.target.closest("#run-clear-request-filter")) {
      state.filter.generation_request_id = "";
      forceReloadRuns();
      return;
    }
    if (e.target.closest("#run-clear-filter")) {
      state.filter = { status: "", claimed_by: "", generation_request_id: state.filter.generation_request_id };
      forceReloadRuns();
      return;
    }

    const logButton = e.target.closest(".run-open-log");
    if (logButton) {
      setView("research-logs");
      return;
    }
  });

  document.addEventListener("change", (e) => {
    const root = document.querySelector('[data-view="research-runs"]');
    if (!root || !root.contains(e.target)) return;

    if (e.target.id === "run-filter-status") {
      state.filter.status = e.target.value;
      forceReloadRuns();
    }
  });

  let timer;
  document.addEventListener("input", (e) => {
    const root = document.querySelector('[data-view="research-runs"]');
    if (!root || !root.contains(e.target)) return;

    if (e.target.id === "run-filter-claimed-by") {
      clearTimeout(timer);
      const value = e.target.value;
      timer = setTimeout(() => {
        state.filter.claimed_by = value;
        forceReloadRuns();
      }, 300);
    }
  });
}
