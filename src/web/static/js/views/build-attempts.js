import { api, del, postJson } from "../api.js";
import { appState } from "../state.js";
import { initIcons } from "../ui/icons.js";
import { showToast } from "../ui/toast.js";
import { confirmDeletion } from "../ui/delete-dialog.js";
import {
  dotTone,
  escapeHtml,
  formatDateTime,
  softPill,
} from "../ui/format.js";

const ACTIVE_POLL_MS = 2500;
const SETTLED_POLL_MS = 12000;
const STATUSES = ["queued", "running", "succeeded", "failed", "lost"];
const CATEGORIES = ["web", "pwn", "re"];
const state = appState.buildAttempts;

export function openBuildAttemptsRoute({ detailId = null, filters = {} } = {}) {
  state.detailId = detailId;
  state.detail = null;
  state.list = null;
  state.filters = {
    ...state.filters,
    ...filters,
  };
}

function isViewActive() {
  return !!document.querySelector('[data-view="build-attempts"]')?.classList.contains("active");
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
  return rows.some((attempt) => attempt.status === "queued" || attempt.status === "running");
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
  if (state.flags.deleting) {
    schedulePoll(ACTIVE_POLL_MS);
    return;
  }
  state.poll.timer = null;
  state.poll.loading = true;
  try {
    if (state.detailId) {
      state.detail = await api(`/api/build-attempts/${state.detailId}`);
    } else {
      state.list = await api(buildListUrl());
    }
    render(appState.data);
    initIcons();
  } catch (err) {
    showToast(err.message, true);
  } finally {
    state.poll.loading = false;
    schedulePoll(needsActivePolling() ? ACTIVE_POLL_MS : SETTLED_POLL_MS);
  }
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
  render(appState.data);
  initIcons();
}

async function ensureDetail(id) {
  if (state.detail !== null || state.flags.detail?.loading) return;
  state.flags.detail = { loading: true, error: null };
  try {
    state.detail = await api(`/api/build-attempts/${id}`);
    state.flags.detail = { loading: false, error: null };
  } catch (err) {
    state.flags.detail = { loading: false, error: err.message };
  }
  render(appState.data);
  initIcons();
}

function buildListUrl() {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(state.filters)) {
    if (value) params.set(key, value);
  }
  const query = params.toString();
  return query ? `/api/build-attempts?${query}` : "/api/build-attempts";
}

async function refreshWithTick() {
  state.flags.refreshing = true;
  render(appState.data);
  initIcons();
  try {
    await api("/api/state");
    state.list = null;
    state.detail = null;
    if (state.detailId) await ensureDetail(state.detailId);
    else await ensureList();
  } catch (err) {
    showToast(err.message, true);
  } finally {
    state.flags.refreshing = false;
    render(appState.data);
    initIcons();
  }
}

async function startBuildWorker() {
  if (!state.detailId) return;
  try {
    const result = await postJson(`/api/build-attempts/${encodeURIComponent(state.detailId)}/worker/start`, {});
    showToast(result.message);
    appState.data = {
      ...(appState.data || {}),
      process: {
        ...(appState.data?.process || {}),
        last_action: "worker",
        last_message: result.message,
      },
    };
  } catch (err) {
    showToast(err.message, true);
  }
}

async function revalidateAttempt(attemptId) {
  if (!attemptId) return;
  state.flags.revalidating = { ...(state.flags.revalidating || {}), [attemptId]: true };
  render(appState.data);
  initIcons();
  try {
    await postJson(`/api/build-attempts/${attemptId}/revalidate`, {});
    showToast("重新校验通过");
    state.detail = null;
    state.list = null;
    await ensureDetail(attemptId);
  } catch (err) {
    showToast(err.message, true);
    state.detail = null;
    await ensureDetail(attemptId);
  } finally {
    state.flags.revalidating = { ...(state.flags.revalidating || {}), [attemptId]: false };
    render(appState.data);
    initIcons();
  }
}

async function retryAttempt(attemptId) {
  if (!attemptId) return;
  state.flags.retrying = { ...(state.flags.retrying || {}), [attemptId]: true };
  render(appState.data);
  initIcons();
  try {
    const result = await postJson(`/api/build-attempts/${attemptId}/retry`, {});
    showToast(`已排队重试构建 ${shortId(result.build_attempt_id)}`);
    state.detailId = result.build_attempt_id;
    state.detail = null;
    state.list = null;
    await ensureDetail(state.detailId);
  } catch (err) {
    showToast(err.message, true);
  } finally {
    state.flags.retrying = { ...(state.flags.retrying || {}), [attemptId]: false };
    render(appState.data);
    initIcons();
  }
}

async function deleteAttempt(attemptId) {
  if (state.flags.deleting) return;
  state.flags.deleting = true;
  render(appState.data);
  initIcons();
  try {
    const choice = await confirmDeletion({
      title: "删除构建运行",
      message: "将删除构建运行记录、队列状态和进度。题目产物默认保留，除非勾选同时删除。",
    });
    if (choice === null) return;
    const query = choice ? "?delete_artifacts=true" : "?delete_artifacts=false";
    const result = await del(`/api/build-attempts/${attemptId}${query}`);
    showToast(result.warnings?.length ? result.warnings[0] : "构建运行已删除");
    state.detailId = null;
    state.detail = null;
    state.list = null;
    window.location.hash = "#/build-attempts";
    await ensureList();
  } catch (err) {
    showToast(err.message, true);
  } finally {
    state.flags.deleting = false;
    render(appState.data);
    initIcons();
  }
}

export function render(data) {
  appState.data = data;
  const root = document.querySelector('[data-view="build-attempts"]');
  if (!root) {
    clearPoll();
    return;
  }

  if (state.detailId) {
    renderDetail(root);
  } else {
    renderList(root);
  }

  requestAnimationFrame(() => initIcons());
  schedulePoll(needsActivePolling() ? ACTIVE_POLL_MS : SETTLED_POLL_MS);
}

function renderList(root) {
  ensureList();
  const flag = state.flags.list || {};
  if (flag.loading && !state.list) {
    root.innerHTML = `<div class="empty">正在加载构建记录...</div>`;
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
          <div class="card-title">构建记录</div>
          <div class="card-subtitle">${rows.length} 条最新构建运行</div>
        </div>
      </div>
      ${renderFilters()}
      ${rows.length ? renderTable(rows) : `<div class="empty card-body">没有匹配的构建记录</div>`}
    </section>
  `;
}

function renderFilters() {
  return `
    <div class="filter-bar filter-bar-vertical-sm">
      <label class="filter-item">状态
        <select id="ba-filter-status" class="filter-select">
          <option value=""${state.filters.status === "" ? " selected" : ""}>全部</option>
          ${STATUSES.map((status) => `<option value="${status}"${state.filters.status === status ? " selected" : ""}>${buildStatusLabel(status)}</option>`).join("")}
        </select>
      </label>
      <label class="filter-item">Worker
        <input id="ba-filter-worker" class="filter-input" value="${escapeHtml(state.filters.worker)}" placeholder="worker">
      </label>
      <label class="filter-item">分类
        <select id="ba-filter-category" class="filter-select">
          <option value=""${state.filters.category === "" ? " selected" : ""}>全部</option>
          ${CATEGORIES.map((category) => `<option value="${category}"${state.filters.category === category ? " selected" : ""}>${category}</option>`).join("")}
        </select>
      </label>
      <label class="filter-item">设计任务
        <input id="ba-filter-design-task" class="filter-input" value="${escapeHtml(state.filters.design_task_id)}" placeholder="design_task_id">
      </label>
      <label class="filter-item">生成请求
        <input id="ba-filter-generation-request" class="filter-input" value="${escapeHtml(state.filters.generation_request_id)}" placeholder="generation_request_id">
      </label>
      <button id="ba-apply-filter" class="filter-clear">应用筛选</button>
      <button id="ba-clear-filter" class="filter-clear">清空</button>
      <button id="ba-refresh" class="btn btn-secondary btn-sm${state.flags.refreshing ? " btn-loading" : ""}">
        <i data-lucide="refresh-cw"></i>刷新
      </button>
    </div>
  `;
}

function renderTable(rows) {
  return `
    <div class="table-container">
      <table class="table">
        <thead>
          <tr>
            <th>题目</th>
            <th>分类</th>
            <th>难度</th>
            <th>状态</th>
            <th>产物</th>
            <th>进度</th>
            <th>Worker</th>
            <th>次数</th>
            <th>创建时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((attempt) => `
            <tr data-build-attempt-id="${escapeHtml(attempt.id)}">
              <td>
                <div class="truncate" style="max-width: 260px;">${escapeHtml(attempt.title || attempt.challenge_id || attempt.id)}</div>
                ${attempt.failure_summary ? `<div style="margin-top: 2px; color: var(--accent-red); font-size: var(--font-xs);">${escapeHtml(attempt.failure_summary)}</div>` : ""}
              </td>
              <td>${softPill(attempt.category || "-")}</td>
              <td>${escapeHtml(attempt.difficulty || "-")}</td>
              <td>${buildStatusIndicator(attempt.status)}</td>
              <td>${artifactPill(attempt.artifact_status)}</td>
              <td>${attempt.percent ?? "-"}</td>
              <td>${escapeHtml(attempt.worker || "-")}</td>
              <td>${attempt.attempt_no}</td>
              <td class="table-cell-time">${escapeHtml(formatDateTime(attempt.created_at))}</td>
              <td>
                <div class="btn-group">
                  <button class="btn btn-secondary btn-xs ba-open-detail">详情</button>
                  ${attempt.status === "failed" || attempt.status === "lost"
                    ? `<button class="btn btn-primary btn-xs ba-retry">重试构建</button>`
                    : ""}
                  <button class="btn btn-danger btn-xs ba-delete" title="删除">
                    <i data-lucide="trash-2"></i>
                  </button>
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
    root.innerHTML = `<div class="empty">正在加载构建运行...</div>`;
    return;
  }
  if (flag.error) {
    root.innerHTML = `<div class="empty">${escapeHtml(flag.error)}</div>`;
    return;
  }
  const attempt = state.detail;
  if (!attempt) return;
  root.innerHTML = `
    <div style="display: flex; align-items: center; justify-content: space-between; gap: var(--space-md); flex-wrap: wrap; margin-bottom: var(--space-md);">
      <button class="btn btn-ghost" id="ba-back">
        <i data-lucide="arrow-left"></i> 返回列表
      </button>
      <div class="btn-group">
        <button id="ba-refresh" class="btn btn-secondary btn-sm">
          <i data-lucide="refresh-cw"></i>刷新
        </button>
        ${attempt.status === "queued"
          ? `<button id="ba-worker" class="btn btn-primary btn-sm"><i data-lucide="play"></i>运行</button>`
          : ""}
        ${attempt.status === "failed"
          ? `<button class="btn btn-secondary btn-sm ba-revalidate" data-build-attempt-id="${escapeHtml(attempt.id)}"><i data-lucide="shield-check"></i>重新校验</button>`
          : ""}
        ${attempt.status === "failed" || attempt.status === "lost"
          ? `<button class="btn btn-primary btn-sm ba-retry" data-build-attempt-id="${escapeHtml(attempt.id)}">重试构建</button>`
          : ""}
        ${["failed", "lost", "succeeded"].includes(attempt.status)
          ? `<button class="btn btn-danger btn-sm ba-delete" data-build-attempt-id="${escapeHtml(attempt.id)}">
              <i data-lucide="trash-2"></i>删除
            </button>`
          : ""}
      </div>
    </div>

    <section class="card card-body" data-build-attempt-id="${escapeHtml(attempt.id)}">
      <div class="flex items-center gap-2" style="flex-wrap: wrap;">
        ${buildStatusIndicator(attempt.status)}
        ${artifactPill(attempt.artifact_status)}
        ${softPill(`第 ${attempt.attempt_no} 次`)}
      </div>
      <h2 style="font-size: var(--font-lg); font-weight: 600; margin-top: var(--space-sm);">构建运行 #${escapeHtml(attempt.attempt_no)}</h2>
      <dl style="margin-top: var(--space-lg); display: grid; gap: var(--space-md); grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));">
        <div><dt>设计任务</dt><dd><button class="btn btn-ghost btn-sm ba-open-design-task">${escapeHtml(shortId(attempt.design_task_id))}</button></dd></div>
        <div><dt>分片</dt><dd class="mono">${escapeHtml(attempt.shard_basename)}</dd></div>
        <div><dt>worker</dt><dd>${escapeHtml(attempt.worker || "-")}</dd></div>
        <div><dt>开始时间</dt><dd>${escapeHtml(formatDateTime(attempt.started_at))}</dd></div>
        <div><dt>完成时间</dt><dd>${escapeHtml(formatDateTime(attempt.finished_at))}</dd></div>
        <div><dt>产物目录</dt><dd class="mono">${escapeHtml(attempt.resulting_challenge_dir || "-")}</dd></div>
      </dl>
      ${failureSummary(attempt) ? `<p style="margin-top: var(--space-md); color: var(--accent-red);">失败原因：${escapeHtml(failureSummary(attempt))}</p>` : ""}
    </section>

    <section class="card" style="margin-top: var(--space-lg);">
      <div class="card-header">
        <div><div class="card-title">尝试历史</div></div>
        <span class="pill">${(attempt.sibling_attempts || []).length}</span>
      </div>
      ${renderSiblingAttempts(attempt)}
    </section>

    <section class="card" style="margin-top: var(--space-lg);">
      <div class="card-header">
        <div><div class="card-title">进度事件</div></div>
        <span class="pill">${(attempt.progress_events || []).length}</span>
      </div>
      ${renderProgressEvents(attempt.progress_events || [])}
    </section>
  `;
}

function renderSiblingAttempts(attempt) {
  const rows = attempt.sibling_attempts || [];
  if (!rows.length) return `<div class="empty card-body">没有尝试历史</div>`;
  return `
    <div class="table-container">
      <table class="table">
        <thead><tr><th>#</th><th>状态</th><th>产物</th><th>Worker</th><th>开始时间</th><th>完成时间</th></tr></thead>
        <tbody>
          ${rows.map((row) => `
            <tr class="ba-history-row" data-build-attempt-id="${escapeHtml(row.id)}">
              <td>${row.attempt_no}</td>
              <td>${buildStatusIndicator(row.status)}</td>
              <td>${artifactPill(row.artifact_status)}</td>
              <td>${escapeHtml(row.worker || "-")}</td>
              <td class="table-cell-time">${escapeHtml(formatDateTime(row.started_at))}</td>
              <td class="table-cell-time">${escapeHtml(formatDateTime(row.finished_at))}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderProgressEvents(events) {
  if (!events.length) return `<div class="empty card-body">没有进度事件</div>`;
  return `
    <div class="card-body" style="display: grid; gap: var(--space-sm);">
      ${events.map((event) => `
        <div class="mono" style="font-size: var(--font-sm); color: ${event.message?.startsWith("carry-forward:") ? "var(--accent-amber)" : "var(--ink-700)"};">
          #${event.id} ${escapeHtml(event.stage)}/${escapeHtml(event.status)}
          ${event.challenge_id ? escapeHtml(event.challenge_id) : "shard"}
          ${escapeHtml(event.message || "")}
        </div>
      `).join("")}
    </div>
  `;
}

function artifactPill(value) {
  return softPill(artifactLabel(value));
}

function buildStatusLabel(status) {
  return {
    queued: "待运行",
    running: "运行中",
    succeeded: "成功",
    failed: "失败",
    lost: "丢失",
  }[status] || status || "未知";
}

function buildStatusIndicator(status) {
  return `<span class="inline-flex items-center text-[12px] text-ink-700"><span class="dot ${dotTone(status)}"></span>${escapeHtml(buildStatusLabel(status))}</span>`;
}

function artifactLabel(value) {
  return {
    present: "已生成",
    missing: "缺失",
    unknown: "未知",
  }[value] || "未知";
}

function failureSummary(attempt) {
  if (attempt.failure_summary) return attempt.failure_summary;
  const events = attempt.progress_events || [];
  for (const event of [...events].reverse()) {
    if (event.stage === "validate" && event.status === "failed") {
      return `校验失败：${failureMessageReason(event.message || "") || "未知原因"}`;
    }
  }
  if (attempt.error === "shard execution failed") return "构建执行失败";
  return attempt.error || "";
}

function failureMessageReason(message) {
  if (message.includes("error=")) {
    return message.split("error=", 2)[1].replace(/^[\s;,]+|[\s;,]+$/g, "");
  }
  return message.trim();
}

function applyFiltersFromInputs() {
  state.filters = {
    status: document.querySelector("#ba-filter-status")?.value || "",
    worker: document.querySelector("#ba-filter-worker")?.value.trim() || "",
    category: document.querySelector("#ba-filter-category")?.value || "",
    design_task_id: document.querySelector("#ba-filter-design-task")?.value.trim() || "",
    generation_request_id: document.querySelector("#ba-filter-generation-request")?.value.trim() || "",
  };
  state.detailId = null;
  state.detail = null;
  state.list = null;
  render(appState.data);
}

function clearFilters() {
  state.filters = {
    status: "",
    worker: "",
    category: "",
    design_task_id: "",
    generation_request_id: "",
  };
  state.detailId = null;
  state.detail = null;
  state.list = null;
  render(appState.data);
}

function openDetail(id) {
  state.detailId = id;
  state.detail = null;
  window.location.hash = `#/build-attempts/${encodeURIComponent(id)}`;
  render(appState.data);
}

function shortId(value) {
  return String(value || "").slice(0, 8);
}

export function bind() {
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && isViewActive()) schedulePoll(ACTIVE_POLL_MS);
  });

  document.addEventListener("click", (event) => {
    const root = document.querySelector('[data-view="build-attempts"]');
    if (!root || !root.contains(event.target)) return;

    if (event.target.closest("#ba-apply-filter")) {
      applyFiltersFromInputs();
      return;
    }
    if (event.target.closest("#ba-clear-filter")) {
      clearFilters();
      return;
    }
    if (event.target.closest("#ba-refresh")) {
      refreshWithTick();
      return;
    }
    if (event.target.closest("#ba-worker")) {
      startBuildWorker();
      return;
    }
    if (event.target.closest("#ba-back")) {
      state.detailId = null;
      state.detail = null;
      window.location.hash = "#/build-attempts";
      render(appState.data);
      return;
    }
    if (event.target.closest(".ba-open-design-task")) {
      appState.view = "design-tasks";
      document.dispatchEvent(new CustomEvent("ctf:open-design-task", {
        detail: { taskId: state.detail?.design_task_id },
      }));
      return;
    }
    const row = event.target.closest("[data-build-attempt-id]");
    const attemptId = row?.dataset.buildAttemptId || state.detailId;
    if (event.target.closest(".ba-open-detail") && attemptId) {
      openDetail(attemptId);
      return;
    }
    if (event.target.closest(".ba-history-row") && attemptId) {
      openDetail(attemptId);
      return;
    }
    if (event.target.closest(".ba-retry") && attemptId) {
      retryAttempt(attemptId);
      return;
    }
    if (event.target.closest(".ba-revalidate") && attemptId) {
      revalidateAttempt(attemptId);
      return;
    }
    if (event.target.closest(".ba-delete") && attemptId) {
      deleteAttempt(attemptId);
    }
  });

  document.addEventListener("change", (event) => {
    const root = document.querySelector('[data-view="build-attempts"]');
    if (!root || !root.contains(event.target)) return;
    if (["ba-filter-status", "ba-filter-category"].includes(event.target.id)) {
      applyFiltersFromInputs();
    }
  });
}
