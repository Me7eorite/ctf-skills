import { api, postJson } from "../api.js";
import { setView } from "../router.js";
import { showToast } from "../ui/toast.js";
import {
  escapeHtml,
  formatDateTime,
  statusIndicator,
  softPill,
} from "../ui/format.js";

const ACTIVE_POLL_MS = 2500;
const SETTLED_POLL_MS = 12000;
const STATUSES = ["draft", "queued", "designing", "designed", "failed", "archived"];

const state = {
  data: null,
  list: null,
  detail: null,
  detailId: null,
  filters: { generation_request_id: "", status: "", category: "" },
  flags: {},
  poll: { timer: null, loading: false },
};

export function showDesignTasksForRequest(requestId) {
  state.filters = {
    ...state.filters,
    generation_request_id: requestId || "",
  };
  state.detailId = null;
  state.detail = null;
  state.list = null;
  setView("design-tasks");
}

async function ensureList() {
  if (state.list !== null || state.flags.list?.loading) return;
  state.flags.list = { loading: true, error: null };
  try {
    state.list = await api(buildListUrl());
    state.flags.list = { loading: false, error: null };
  } catch (err) {
    state.flags.list = { loading: false, error: err.message };
  }
  render(state.data);
  window.lucide?.createIcons();
}

async function ensureDetail(id) {
  if (state.detail !== null || state.flags.detail?.loading) return;
  state.flags.detail = { loading: true, error: null };
  try {
    state.detail = await api(`/api/design-tasks/${id}`);
    state.flags.detail = { loading: false, error: null };
  } catch (err) {
    state.flags.detail = { loading: false, error: err.message };
  }
  render(state.data);
  window.lucide?.createIcons();
}

function buildListUrl() {
  const params = new URLSearchParams();
  if (state.filters.generation_request_id) {
    params.set("generation_request_id", state.filters.generation_request_id);
  }
  if (state.filters.status) params.set("status", state.filters.status);
  if (state.filters.category) params.set("category", state.filters.category);
  const query = params.toString();
  return query ? `/api/design-tasks?${query}` : "/api/design-tasks";
}

function isViewActive() {
  return !!document.querySelector('[data-view="design-tasks"]')?.classList.contains("active");
}

function clearPoll() {
  if (state.poll.timer) {
    window.clearTimeout(state.poll.timer);
    state.poll.timer = null;
  }
  state.poll.loading = false;
}

function needsActivePolling() {
  const rows = state.detail ? [state.detail] : (state.list || []);
  return rows.some((task) => task.status === "queued" || task.status === "designing");
}

function schedulePoll(delay = SETTLED_POLL_MS) {
  if (!isViewActive()) {
    clearPoll();
    return;
  }
  if (document.hidden) return;
  if (state.poll.timer) window.clearTimeout(state.poll.timer);
  state.poll.timer = window.setTimeout(poll, delay);
}

async function poll() {
  if (!isViewActive() || document.hidden || state.poll.loading) return;
  state.poll.timer = null;
  state.poll.loading = true;
  try {
    if (state.detailId) {
      state.detail = await api(`/api/design-tasks/${state.detailId}`);
    } else {
      state.list = await api(buildListUrl());
    }
    render(state.data);
    window.lucide?.createIcons();
  } catch (err) {
    showToast(err.message, true);
  } finally {
    state.poll.loading = false;
    schedulePoll(needsActivePolling() ? ACTIVE_POLL_MS : SETTLED_POLL_MS);
  }
}

async function reloadList() {
  state.list = null;
  await ensureList();
}

async function reloadDetail() {
  state.detail = null;
  if (state.detailId) await ensureDetail(state.detailId);
}

async function transitionTask(taskId, action) {
  try {
    await postJson(`/api/design-tasks/${taskId}/${action}`, {});
    showToast(`Task ${action}d`);
    if (state.detailId) {
      await reloadDetail();
    } else {
      await reloadList();
    }
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
    state.detailId = taskId;
    await reloadDetail();
  } catch (err) {
    showToast(err.message, true);
  } finally {
    state.flags.designing = { ...(state.flags.designing || {}), [taskId]: false };
    render(state.data);
    window.lucide?.createIcons();
  }
}

export function render(data) {
  state.data = data;
  const root = document.querySelector('[data-view="design-tasks"]');
  if (!root) {
    clearPoll();
    return;
  }

  if (state.detailId) {
    renderDetail(root);
  } else {
    renderList(root);
  }
  schedulePoll(needsActivePolling() ? ACTIVE_POLL_MS : SETTLED_POLL_MS);
}

function renderList(root) {
  ensureList();
  const flag = state.flags.list || {};
  if (flag.loading && !state.list) {
    root.innerHTML = `<div class="empty">Loading design tasks...</div>`;
    return;
  }
  if (flag.error) {
    root.innerHTML = `<div class="empty">${escapeHtml(flag.error)}</div>`;
    return;
  }
  const rows = state.list || [];
  root.innerHTML = `
    <section class="card">
      <div class="card-header">
        <div>
          <div class="card-title">Design Tasks</div>
          <div class="card-subtitle">Plan, release, and inspect challenge design work.</div>
        </div>
        <span class="pill">${rows.length} rows</span>
      </div>
      <div class="filter-bar">
        <label class="filter-item">Request
          <input id="dt-filter-request" class="filter-input" value="${escapeHtml(state.filters.generation_request_id)}" placeholder="generation_request_id">
        </label>
        <label class="filter-item">Status
          <select id="dt-filter-status" class="filter-select">
            <option value=""${state.filters.status === "" ? " selected" : ""}>All</option>
            ${STATUSES.map((status) => `<option value="${escapeHtml(status)}"${state.filters.status === status ? " selected" : ""}>${escapeHtml(status)}</option>`).join("")}
          </select>
        </label>
        <label class="filter-item">Category
          <input id="dt-filter-category" class="filter-input" value="${escapeHtml(state.filters.category)}" placeholder="web">
        </label>
        <button id="dt-apply-filter" class="filter-clear">Apply</button>
        <button id="dt-clear-filter" class="filter-clear">Clear</button>
      </div>
      ${rows.length ? renderTable(rows) : `<div class="empty card-body">No matching design tasks</div>`}
    </section>
  `;
}

function renderTable(rows) {
  return `
    <div class="table-container">
      <table class="table">
        <thead>
          <tr>
            <th>Request</th>
            <th>Task</th>
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
          ${rows.map((task) => `
            <tr data-design-task-id="${escapeHtml(task.id)}">
              <td>
                <button class="btn btn-ghost btn-sm dt-open-request" title="${escapeHtml(task.generation_request_id)}">
                  ${escapeHtml(shortId(task.generation_request_id))}
                </button>
              </td>
              <td class="table-cell-id">${task.task_no}</td>
              <td class="mono">${escapeHtml(task.challenge_id)}</td>
              <td><div class="truncate" style="max-width: 280px;">${escapeHtml(task.title)}</div></td>
              <td>${escapeHtml(task.difficulty)}</td>
              <td><div class="truncate" style="max-width: 220px;">${escapeHtml(task.primary_technique)}</div></td>
              <td style="text-align: right;">${(task.finding_ids || []).length}</td>
              <td>${escapeHtml(task.status)}</td>
              <td>
                <div class="btn-group design-task-actions">
                  <button class="btn btn-secondary btn-sm dt-open-detail" title="Details">
                    <i data-lucide="panel-right-open"></i>
                  </button>
                  <button class="btn btn-secondary btn-sm dt-queue"${task.status === "draft" ? "" : " disabled"}>Queue</button>
                  <button class="btn btn-ghost btn-sm dt-archive"${(task.status === "draft" || task.status === "queued") ? "" : " disabled"}>Archive</button>
                  <button class="btn btn-primary btn-sm dt-design"${task.status === "queued" && !state.flags.designing?.[task.id] ? "" : " disabled"}>Design</button>
                </div>
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderDetail(root) {
  ensureDetail(state.detailId);
  const flag = state.flags.detail || {};
  if (flag.loading && !state.detail) {
    root.innerHTML = `<div class="empty">Loading design task...</div>`;
    return;
  }
  if (flag.error) {
    root.innerHTML = `<div class="empty">${escapeHtml(flag.error)}</div>`;
    return;
  }
  const task = state.detail;
  if (!task) return;
  const attempts = task.attempts || [];
  const latestAttempt = attempts.length ? attempts[attempts.length - 1] : null;
  const latestDesign = task.latest_design || null;
  const isDesigning = !!state.flags.designing?.[task.id];
  root.innerHTML = `
    <div style="display: flex; align-items: center; justify-content: space-between; gap: var(--space-md); flex-wrap: wrap; margin-bottom: var(--space-md);">
      <button class="btn btn-ghost" id="dt-back">
        <i data-lucide="arrow-left"></i> Back to list
      </button>
      <div class="btn-group">
        <button class="btn btn-secondary btn-sm dt-queue"${task.status === "draft" ? "" : " disabled"}>Queue</button>
        <button class="btn btn-ghost btn-sm dt-archive"${(task.status === "draft" || task.status === "queued") ? "" : " disabled"}>Archive</button>
        <button class="btn btn-primary btn-sm dt-design${isDesigning ? " btn-loading" : ""}"${task.status === "queued" && !isDesigning ? "" : " disabled"}>
          <i data-lucide="sparkles"></i> Design
        </button>
      </div>
    </div>

    <section class="card card-body" data-design-task-id="${escapeHtml(task.id)}">
      <div class="flex items-center gap-2" style="flex-wrap: wrap;">
        ${softPill(task.category)}
        ${statusIndicator(task.status)}
        ${latestAttempt ? statusIndicator(latestAttempt.status) : softPill("No attempts")}
      </div>
      <h2 style="font-size: var(--font-lg); font-weight: 600; margin-top: var(--space-sm);">${escapeHtml(task.title)}</h2>
      <div class="mono" style="font-size: var(--font-sm); color: var(--ink-500); margin-top: 2px;">${escapeHtml(task.id)}</div>
      <dl style="margin-top: var(--space-lg); display: grid; gap: var(--space-md); grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));">
        <div><dt>generation_request</dt><dd><button class="btn btn-ghost btn-sm dt-open-request" title="${escapeHtml(task.generation_request_id)}">${escapeHtml(shortId(task.generation_request_id))}</button></dd></div>
        <div><dt>task_no</dt><dd>${escapeHtml(task.task_no)}</dd></div>
        <div><dt>challenge_id</dt><dd class="mono">${escapeHtml(task.challenge_id)}</dd></div>
        <div><dt>difficulty</dt><dd>${escapeHtml(task.difficulty)}</dd></div>
        <div><dt>technique</dt><dd>${escapeHtml(task.primary_technique)}</dd></div>
        <div><dt>points</dt><dd>${escapeHtml(task.points)}</dd></div>
      </dl>
      <p style="margin-top: var(--space-md); color: var(--ink-600);">${escapeHtml(task.learning_objective || "")}</p>
    </section>

    <section class="card" style="margin-top: var(--space-lg);">
      <div class="card-header">
        <div><div class="card-title">Attempts</div></div>
        <span class="pill">${attempts.length}</span>
      </div>
      ${attempts.length ? renderAttempts(attempts) : `<div class="empty card-body">No design attempts yet</div>`}
    </section>

    <section class="card" style="margin-top: var(--space-lg);">
      <div class="card-header">
        <div><div class="card-title">Latest Design</div></div>
        ${latestDesign ? qualityGatePill(latestDesign.quality_gate_passed) : ""}
      </div>
      <div class="card-body">
        ${latestDesign ? renderLatestDesign(latestDesign) : `<div class="empty">No draft design yet</div>`}
      </div>
    </section>
  `;
}

function renderAttempts(attempts) {
  return `
    <div class="table-container">
      <table class="table">
        <thead><tr><th>#</th><th>Status</th><th>Started</th><th>Finished</th><th>Artifacts</th></tr></thead>
        <tbody>
          ${attempts.map((attempt) => `
            <tr>
              <td class="table-cell-id">${attempt.attempt}</td>
              <td>${statusIndicator(attempt.status)}</td>
              <td class="table-cell-time">${escapeHtml(formatDateTime(attempt.started_at))}</td>
              <td class="table-cell-time">${escapeHtml(formatDateTime(attempt.finished_at))}</td>
              <td>
                <div class="btn-group">
                  ${attempt.prompt_artifact_url ? `<a class="btn btn-secondary btn-sm" href="${escapeHtml(attempt.prompt_artifact_url)}" target="_blank" rel="noopener">Prompt</a>` : ""}
                  ${attempt.log_artifact_url ? `<a class="btn btn-secondary btn-sm" href="${escapeHtml(attempt.log_artifact_url)}" target="_blank" rel="noopener">Log</a>` : ""}
                </div>
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderLatestDesign(design) {
  return `
    <div class="design-summary">
      <div class="design-summary-line">${escapeHtml(design.summary || "-")}</div>
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
  return softPill(
    passed ? "Quality passed" : "Quality failed",
    passed ? "text-emerald-700 bg-emerald-50" : "text-rose-700 bg-rose-50",
  );
}

function shortId(value) {
  return String(value || "").slice(0, 8);
}

function openRequest(requestId) {
  document.dispatchEvent(
    new CustomEvent("ctf:open-research-request", { detail: { requestId } }),
  );
}

function applyFiltersFromInputs() {
  state.filters = {
    generation_request_id: document.querySelector("#dt-filter-request")?.value.trim() || "",
    status: document.querySelector("#dt-filter-status")?.value || "",
    category: document.querySelector("#dt-filter-category")?.value.trim() || "",
  };
  state.list = null;
  render(state.data);
}

export function bind() {
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && isViewActive()) {
      schedulePoll(ACTIVE_POLL_MS);
    }
  });

  document.addEventListener("click", (event) => {
    const root = document.querySelector('[data-view="design-tasks"]');
    if (!root || !root.contains(event.target)) return;

    if (event.target.closest("#dt-apply-filter")) {
      applyFiltersFromInputs();
      return;
    }
    if (event.target.closest("#dt-clear-filter")) {
      state.filters = { generation_request_id: "", status: "", category: "" };
      state.list = null;
      render(state.data);
      return;
    }
    if (event.target.closest("#dt-back")) {
      state.detailId = null;
      state.detail = null;
      render(state.data);
      return;
    }

    const row = event.target.closest("[data-design-task-id]");
    const taskId = row?.dataset.designTaskId || state.detailId;
    if (event.target.closest(".dt-open-request")) {
      const task = state.detail || (state.list || []).find((item) => item.id === taskId);
      if (task?.generation_request_id) openRequest(task.generation_request_id);
      return;
    }
    if (event.target.closest(".dt-open-detail") && taskId) {
      state.detailId = taskId;
      state.detail = null;
      render(state.data);
      return;
    }
    if (event.target.closest(".dt-queue") && taskId) {
      transitionTask(taskId, "queue");
      return;
    }
    if (event.target.closest(".dt-archive") && taskId) {
      transitionTask(taskId, "archive");
      return;
    }
    if (event.target.closest(".dt-design") && taskId) {
      designTaskNow(taskId);
    }
  });

  document.addEventListener("change", (event) => {
    const root = document.querySelector('[data-view="design-tasks"]');
    if (!root || !root.contains(event.target)) return;
    if (event.target.id === "dt-filter-status") applyFiltersFromInputs();
  });
}
