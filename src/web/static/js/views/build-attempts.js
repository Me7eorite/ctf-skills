import { api, del, postJson } from "../api.js";
import { appState } from "../state.js";
import { initIcons } from "../ui/icons.js";
import { showToast } from "../ui/toast.js";
import { confirmDeletion } from "../ui/delete-dialog.js";
import {
  categoryLabel,
  dotTone,
  escapeHtml,
  formatDateTime,
  softPill,
} from "../ui/format.js";

const ACTIVE_POLL_MS = 1000;
const START_REFRESH_MS = 300;
const SETTLED_POLL_MS = 12000;
const FILTER_INTERACTION_HOLD_MS = 1500;
const LIST_LIMIT = 200;
const STATUSES = ["queued", "running", "succeeded", "failed", "lost"];
const CATEGORIES = ["web", "pwn", "re"];
const VALIDATION_FAILURE_LABELS = {
  timeout: "校验超时",
  "service-readiness": "服务就绪失败",
  contract: "产物合约失败",
  solver: "exp 利用失败",
};
const VALIDATION_DETAIL_LABELS = {
  pwn_prompt_eof: "菜单/提示同步 EOF",
  pwn_service_readiness_failed: "服务未就绪",
  pwn_port_only_readiness: "端口探活不足",
  pwn_bad_readiness_probe: "就绪探针不可靠",
  missing_dependency: "缺少依赖",
  flag_mismatch: "flag 不匹配",
  nonzero_exit: "validate.sh 非零退出",
  timeout: "超时",
};
const state = appState.buildAttempts;
const detailEventNodes = new Map();
const EMPTY_FILTERS = {
  status: "",
  worker: "",
  category: "",
  design_task_id: "",
  generation_request_id: "",
};

export function openBuildAttemptsRoute({ detailId = null, filters = {} } = {}) {
  state.detailId = detailId;
  state.detail = null;
  state.list = null;
  state.filters = {
    ...EMPTY_FILTERS,
    ...filters,
  };
  syncFilterDraft();
}

export function invalidateBuildAttempts() {
  state.list = null;
  state.detail = null;
  state.lanePools = null;
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
  const activeRows = rows.some((attempt) => attempt.status === "queued" || attempt.status === "running");
  const activePools = (state.lanePools || []).some((pool) => pool.running);
  return activeRows || activePools;
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
  const root = document.querySelector('[data-view="build-attempts"]');
  if (!state.detailId && root && isListInteractionProtected(root)) {
    schedulePoll(ACTIVE_POLL_MS);
    return;
  }
  state.poll.timer = null;
  state.poll.loading = true;
  try {
    if (state.detailId) {
      const nextDetail = await api(`/api/build-attempts/${state.detailId}`);
      if (patchDetailEvents(nextDetail)) {
        state.detail = nextDetail;
      } else {
        state.detail = nextDetail;
        render(appState.data);
        initIcons();
      }
    } else {
      const [list, pools] = await Promise.all([
        api(buildListUrl()),
        fetchLanePools(),
      ]);
      state.list = list;
      state.lanePools = pools;
      render(appState.data);
      initIcons();
    }
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
    const [list, pools] = await Promise.all([
      api(buildListUrl()),
      fetchLanePools(),
    ]);
    state.list = list;
    state.lanePools = pools;
    state.flags.list = { loading: false, error: null };
  } catch (err) {
    state.flags.list = { loading: false, error: err.message };
  }
  render(appState.data);
  initIcons();
}

async function fetchLanePools() {
  const result = await api("/api/build-attempts/worker/pools");
  return Array.isArray(result.pools) ? result.pools : [];
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
  params.set("limit", String(LIST_LIMIT));
  for (const [key, value] of Object.entries(state.filters)) {
    if (value) params.set(key, value);
  }
  const query = params.toString();
  return query ? `/api/build-attempts?${query}` : "/api/build-attempts";
}

function syncFilterDraft() {
  state.filterDraft = { ...state.filters };
}

function readFilterInputs() {
  return {
    status: document.querySelector("#ba-filter-status")?.value || "",
    worker: document.querySelector("#ba-filter-worker")?.value.trim() || "",
    category: document.querySelector("#ba-filter-category")?.value || "",
    design_task_id: document.querySelector("#ba-filter-design-task")?.value.trim() || "",
    generation_request_id: document.querySelector("#ba-filter-generation-request")?.value.trim() || "",
  };
}

function updateFilterDraftFromInputs() {
  state.filterDraft = readFilterInputs();
}

function markFilterInteraction(duration = FILTER_INTERACTION_HOLD_MS) {
  state.filterInteractionUntil = Date.now() + duration;
}

function clearFilterInteraction() {
  state.filterInteractionUntil = 0;
}

function isFilterControl(element) {
  return Boolean(element?.id?.startsWith("ba-filter-") || element?.id === "ba-lane-count");
}

function isListInteractionProtected(root) {
  const active = document.activeElement;
  if (active && root.contains(active) && isFilterControl(active)) return true;
  return Boolean(state.filterInteractionUntil && Date.now() < state.filterInteractionUntil);
}

function shouldDeferListRender(root) {
  return state.list !== null
    && root.querySelector(".ba-list-card")
    && isListInteractionProtected(root);
}

function captureFilterFocus(root) {
  const active = document.activeElement;
  if (!active || !root.contains(active) || !active.id?.startsWith("ba-filter-")) return null;
  return {
    id: active.id,
    selectionStart: typeof active.selectionStart === "number" ? active.selectionStart : null,
    selectionEnd: typeof active.selectionEnd === "number" ? active.selectionEnd : null,
  };
}

function restoreFilterFocus(snapshot) {
  if (!snapshot) return;
  requestAnimationFrame(() => {
    const input = document.getElementById(snapshot.id);
    if (!input) return;
    input.focus({ preventScroll: true });
    if (
      snapshot.selectionStart !== null
      && snapshot.selectionEnd !== null
      && typeof input.setSelectionRange === "function"
    ) {
      input.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd);
    }
  });
}

function scheduleFilterApply(delay = 450) {
  if (state.filterTimer) window.clearTimeout(state.filterTimer);
  state.filterTimer = window.setTimeout(() => {
    state.filterTimer = null;
    applyFiltersFromInputs();
  }, delay);
}

async function refreshWithTick() {
  state.flags.refreshing = true;
  render(appState.data);
  initIcons();
  try {
    appState.data = await api("/api/ui-state");
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
    showToast(`${result.message}（超时 ${result.effective_timeout_seconds}s，${result.timeout_source}）`);
    appState.data = {
      ...(appState.data || {}),
      process: {
        ...(appState.data?.process || {}),
        last_action: "worker",
        last_message: result.message,
      },
    };
    state.detail = null;
    state.list = null;
    await ensureDetail(state.detailId);
    schedulePoll(START_REFRESH_MS);
  } catch (err) {
    showToast(err.message, true);
  }
}

async function stopBuildWorker() {
  try {
    const result = await postJson("/api/build-attempts/worker/stop", {});
    showToast(result.message || "构建任务已结束");
    state.detail = null;
    state.list = null;
    state.lanePools = null;
    if (state.detailId) await ensureDetail(state.detailId);
    else await ensureList();
    schedulePoll(START_REFRESH_MS);
  } catch (err) {
    showToast(err.message, true);
  }
}

async function startCurrentQueue() {
  const payload = {};
  if (state.filters.category) payload.category = state.filters.category;
  if (state.filters.generation_request_id) {
    payload.generation_request_id = state.filters.generation_request_id;
  }
  try {
    const result = await postJson("/api/build-attempts/queue/start", payload);
    showToast(`顺序队列已启动 · 共 ${result.queue_length} 个任务`);
    state.selection.clear();
    state.list = null;
    await ensureList();
    schedulePoll(START_REFRESH_MS);
  } catch (err) {
    showToast(err.message, true);
  }
}

async function startSelectedQueue() {
  if (!state.selection.size) return;
  const rows = state.list || [];
  const orderedIds = selectedQueuedIds(rows);
  if (!orderedIds.length) {
    showToast("选中的题目均已不在 queued 状态", true);
    return;
  }
  try {
    const result = await postJson(
      "/api/build-attempts/worker/start-sequential",
      { build_attempt_ids: orderedIds },
    );
    showToast(`顺序队列已启动 · 共 ${result.queue_length} 个任务`);
    state.selection.clear();
    state.list = null;
    await ensureList();
    schedulePoll(START_REFRESH_MS);
  } catch (err) {
    showToast(err.message, true);
  }
}

async function startSelectedLanes() {
  if (!state.selection.size) return;
  const rows = state.list || [];
  const orderedIds = selectedQueuedIds(rows);
  if (!orderedIds.length) {
    showToast("选中的题目均已不在 queued 状态", true);
    return;
  }
  try {
    const result = await postJson(
      "/api/build-attempts/worker/start-sequential-lanes",
      {
        build_attempt_ids: orderedIds,
        lanes: currentLaneCount(),
      },
    );
    showToast(`多队列已启动 · ${result.lane_count} 条 lane · 共 ${result.queue_length} 个任务`);
    state.selection.clear();
    state.lanePools = result.pool ? [result.pool, ...(state.lanePools || [])] : state.lanePools;
    state.list = null;
    await ensureList();
    schedulePoll(START_REFRESH_MS);
  } catch (err) {
    showToast(err.message, true);
  }
}

async function retrySelectedLanes() {
  if (!state.selection.size) return;
  const rows = state.list || [];
  const orderedIds = selectedRetryableIds(rows);
  if (!orderedIds.length) {
    showToast("选中的题目均不需要重试", true);
    return;
  }
  try {
    const result = await postJson(
      "/api/build-attempts/worker/retry-sequential-lanes",
      {
        build_attempt_ids: orderedIds,
        lanes: currentLaneCount(),
      },
    );
    showToast(`重试多队列已启动 · ${result.lane_count} 条 lane · 共 ${result.queue_length} 个任务`);
    state.selection.clear();
    state.lanePools = result.pool ? [result.pool, ...(state.lanePools || [])] : state.lanePools;
    state.list = null;
    await ensureList();
    schedulePoll(START_REFRESH_MS);
  } catch (err) {
    showToast(err.message, true);
  }
}

function selectedQueuedIds(rows) {
  return rows
    .filter((row) => row.status === "queued" && state.selection.has(row.id))
    .map((row) => row.id);
}

function selectedRetryableIds(rows) {
  return rows
    .filter((row) => (row.status === "failed" || row.status === "lost") && state.selection.has(row.id))
    .map((row) => row.id);
}

function currentLaneCount() {
  const input = document.querySelector("#ba-lane-count");
  const raw = Number(input?.value || state.laneCount || 4);
  const lanes = Number.isFinite(raw) ? Math.floor(raw) : 4;
  state.laneCount = Math.min(6, Math.max(1, lanes));
  return state.laneCount;
}

function toggleRowSelection(attemptId, checked) {
  if (!attemptId) return;
  if (checked) state.selection.add(attemptId);
  else state.selection.delete(attemptId);
  render(appState.data);
  initIcons();
}

function toggleSelectAll(checked) {
  const rows = state.list || [];
  for (const row of rows) {
    if (!["queued", "failed", "lost"].includes(row.status)) continue;
    if (checked) state.selection.add(row.id);
    else state.selection.delete(row.id);
  }
  render(appState.data);
  initIcons();
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
    state.detailId = result.build_attempt_id;
    state.detail = null;
    state.list = null;
    await ensureDetail(state.detailId);
    const iterationLabel = result.iteration_no ? `第 ${result.iteration_no} 轮` : "新一轮";
    showToast(`已排队${iterationLabel}重试 ${shortId(result.build_attempt_id)}`);
    schedulePoll(START_REFRESH_MS);
  } catch (err) {
    showToast(err.message, true);
  } finally {
    state.flags.retrying = { ...(state.flags.retrying || {}), [attemptId]: false };
    render(appState.data);
    initIcons();
  }
}

async function repairAttempt(attemptId) {
  if (!attemptId) return;
  state.flags.repairing = { ...(state.flags.repairing || {}), [attemptId]: true };
  render(appState.data);
  initIcons();
  try {
    const result = await postJson(`/api/build-attempts/${attemptId}/repair`, {});
    if (result.status === "succeeded") {
      showToast("AI 修复并重新验收通过");
    } else {
      showToast(result.failure_summary || "AI 修复后重新验收未通过", true);
    }
    state.detailId = result.build_attempt_id || attemptId;
    state.detail = null;
    state.list = null;
    await ensureDetail(state.detailId);
  } catch (err) {
    showToast(err.message, true);
  } finally {
    state.flags.repairing = { ...(state.flags.repairing || {}), [attemptId]: false };
    render(appState.data);
    initIcons();
  }
}

async function cleanRebuildAttempt(attemptId) {
  if (!attemptId) return;
  const confirmed = window.confirm("干净重建不会复用上次输出或进度，确定继续？");
  if (!confirmed) return;
  // One UUID per user-visible button press. Only retries of this same request
  // may reuse it; a later click must generate a new key.
  const idempotencyKey = crypto.randomUUID();
  state.flags.retrying = { ...(state.flags.retrying || {}), [attemptId]: true };
  render(appState.data);
  initIcons();
  try {
    const result = await postJson(`/api/build-attempts/${attemptId}/clean-rebuild`, {
      confirmed: true,
      idempotency_key: idempotencyKey,
    });
    showToast(`已排队干净重建 ${shortId(result.build_attempt_id)}`);
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
    requestAnimationFrame(rebuildDetailEventNodes);
  } else {
    detailEventNodes.clear();
    if (shouldDeferListRender(root)) {
      schedulePoll(ACTIVE_POLL_MS);
      return;
    }
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
  pruneSelection(rows);
  const selectedCount = state.selection.size;
  const selectedQueuedCount = selectedQueuedIds(rows).length;
  const selectedRetryableCount = selectedRetryableIds(rows).length;
  const summary = summarizeBuildRows(rows);
  const hasActiveWorker = rows.some((row) => row.status === "running") || (state.lanePools || []).some((pool) => pool.running);
  const focusSnapshot = captureFilterFocus(root);
  root.innerHTML = `
    ${renderBuildReadinessWarning()}
    <div class="ba-page-header">
      <div>
        <h2 class="ba-page-title">构建记录</h2>
        <p class="ba-page-desc">队列、执行和产物状态按当前筛选实时展示。</p>
      </div>
      <div class="ba-page-actions">
        <button id="ba-start-selected" class="btn btn-primary btn-sm" ${selectedQueuedCount ? "" : "disabled"}
          title="按表格中的勾选顺序顺序执行所选 queued 题目">
          <i data-lucide="play"></i>启动选中（单队列）${selectedQueuedCount ? `· ${selectedQueuedCount}` : ""}
        </button>
        <label class="ba-lane-control" title="把选中任务拆成多条顺序队列；每条 lane 内仍按顺序执行">
          <span>lane</span>
          <input id="ba-lane-count" type="number" min="1" max="6" step="1" value="${escapeHtml(state.laneCount || 4)}">
        </label>
        <button id="ba-start-selected-lanes" class="btn btn-primary btn-sm" ${selectedQueuedCount ? "" : "disabled"}
          title="按表格顺序 round-robin 拆分选中 queued 题目，多条顺序队列并发运行">
          <i data-lucide="columns-3"></i>启动多队列${selectedQueuedCount ? `（${selectedQueuedCount}）` : ""}
        </button>
        <button id="ba-retry-selected-lanes" class="btn btn-secondary btn-sm" ${selectedRetryableCount ? "" : "disabled"}
          title="对选中的 failed/lost 题目创建重试轮次，并按 lane 并发启动">
          <i data-lucide="rotate-cw"></i>重试多队列${selectedRetryableCount ? `（${selectedRetryableCount}）` : ""}
        </button>
        <button id="ba-start-queue" class="btn btn-secondary btn-sm"
          title="按当前的分类/生成请求筛选，启动全部 queued 题目（按创建时间从早到晚，最多 100 条）">
          <i data-lucide="list-ordered"></i>启动全部待运行
        </button>
        ${hasActiveWorker
          ? `<button id="ba-stop-worker" class="btn btn-danger btn-sm" title="结束当前构建 worker">
              <i data-lucide="square"></i>结束运行中
            </button>`
          : ""}
      </div>
    </div>

    <div class="ba-summary-grid">
      ${renderBuildMetric("全部记录", rows.length, "layers-3", "neutral")}
      ${renderBuildMetric("待运行", summary.queued, "clock", "warning")}
      ${renderBuildMetric("运行中", summary.running, "activity", "info")}
      ${renderBuildMetric("失败/丢失", summary.failed + summary.lost, "triangle-alert", "danger")}
    </div>

    <section class="card ba-list-card">
      <div class="ba-list-summary">
        <div>
          <div class="card-title">构建记录</div>
          <div class="card-subtitle">${rows.length} 条记录${selectedCount ? ` · 已选 ${selectedCount} 条` : ""}${Object.values(state.filters).some(Boolean) ? " · 已应用筛选" : ""}</div>
        </div>
        <span class="pill">待运行 ${summary.queued} · 运行中 ${summary.running}</span>
      </div>
      ${renderFilters()}
      ${rows.length ? `${renderTable(rows)}${renderAttemptCards(rows)}` : `<div class="empty card-body">没有匹配的构建记录</div>`}
    </section>
  `;
  restoreFilterFocus(focusSnapshot);
}

function summarizeBuildRows(rows) {
  return rows.reduce((acc, row) => {
    if (row.status in acc) acc[row.status] += 1;
    return acc;
  }, { queued: 0, running: 0, succeeded: 0, failed: 0, lost: 0 });
}

function renderBuildMetric(label, value, icon, tone) {
  return `
    <article class="ba-metric ba-metric-${tone}">
      <i data-lucide="${icon}"></i>
      <div>
        <strong>${value}</strong>
        <span>${escapeHtml(label)}</span>
      </div>
    </article>
  `;
}

function renderProgressCell(attempt) {
  const sequentialNotice = sequentialAttemptNotice(attempt);
  if (sequentialNotice) return sequentialNotice;
  // percent 来自 Hermes 自报的 document/build/... passed 事件，不代表磁盘证据校验
  // 通过；在 failed/lost 终态下若仍只渲染 96% / 99% 会让人误以为"接近完成"。
  // 这里把"事件进度"与"终态结果"分开展示。
  const value = attempt.percent ?? "-";
  if (attempt.status === "failed" || attempt.status === "lost") {
    return `
      <div>${value}</div>
      <div class="ba-progress-failed">
        · 校验失败
      </div>
    `;
  }
  return `${value}`;
}

function executionCountLabel(attempt) {
  const latest = latestExecutionIteration(attempt);
  if (latest > 0) return `第 ${latest} 轮`;
  return `第 ${attempt.attempt_no || 1} 次`;
}

function latestExecutionIteration(attempt) {
  const direct = Number(attempt.latest_execution_iteration);
  if (Number.isFinite(direct) && direct > 0) return Math.floor(direct);
  const executions = Array.isArray(attempt.executions) ? attempt.executions : [];
  return executions.reduce((max, row) => {
    const value = Number(row?.iteration_no);
    return Number.isFinite(value) && value > max ? Math.floor(value) : max;
  }, 0);
}

function timeoutLabel(attempt) {
  const seconds = Number(attempt.effective_timeout_seconds);
  if (!Number.isFinite(seconds) || seconds <= 0) return "-";
  return `${Math.floor(seconds)}s (${attempt.timeout_source || "-"})`;
}

function designTaskLabel(attempt) {
  const title = String(attempt.title || "").trim();
  if (title && !/^题目设计\s+[0-9a-f-]{6,}$/i.test(title)) return title;
  const taskNo = Number(attempt.task_no);
  if (Number.isFinite(taskNo) && taskNo > 0) return `第 ${Math.floor(taskNo)} 题设计`;
  if (attempt.challenge_id) return `挑战 ${attempt.challenge_id}`;
  return "构建题目";
}

function shortShardName(value) {
  const text = String(value || "-");
  const match = text.match(/^([0-9a-f-]{36})(\.iter-\d{3}\.json|\.json)$/i);
  if (match) return `${shortId(match[1])}...${match[2].replace(/^\./, "")}`;
  if (text.length <= 28) return text;
  return `${text.slice(0, 8)}...${text.slice(-16)}`;
}

function fileQueueToken(value) {
  return `
    <span class="ba-file-token mono" title="${escapeHtml(value || "-")}">
      ${escapeHtml(shortShardName(value))}
    </span>
  `;
}

function designTaskLink(attempt) {
  return `
    <button class="ba-design-link ba-open-design-task" title="打开关联设计 ${escapeHtml(attempt.design_task_id || "")}">
      <span>查看</span>
      <strong>${escapeHtml(designTaskLabel(attempt))}</strong>
      <i data-lucide="arrow-up-right"></i>
    </button>
  `;
}

function sequentialAttemptNotice(attempt) {
  const result = appState.data?.sequential_worker_result;
  const outcomes = Array.isArray(result?.outcomes) ? result.outcomes : [];
  const outcome = outcomes.find((item) => item?.status === "aborted" && item?.shard === attempt.id);
  if (!outcome) return "";
  return `
    <div>已中止</div>
    <div class="ba-progress-aborted">待重提</div>
  `;
}

function pruneSelection(rows) {
  if (!state.selection.size) return;
  const eligible = new Set(rows.filter((r) => ["queued", "failed", "lost"].includes(r.status)).map((r) => r.id));
  for (const id of [...state.selection]) {
    if (!eligible.has(id)) state.selection.delete(id);
  }
}

function renderFilters() {
  const draft = state.filterDraft || state.filters;
  return `
    <div class="filter-bar ba-filters">
      <div class="ba-filter-grid">
        <label class="filter-item ba-filter-item">状态
          <select id="ba-filter-status" class="filter-select">
            <option value=""${draft.status === "" ? " selected" : ""}>全部</option>
            ${STATUSES.map((status) => `<option value="${status}"${draft.status === status ? " selected" : ""}>${buildStatusLabel(status)}</option>`).join("")}
          </select>
        </label>
        <label class="filter-item ba-filter-item">Worker
          <input id="ba-filter-worker" class="filter-input" value="${escapeHtml(draft.worker)}" placeholder="worker">
        </label>
        <label class="filter-item ba-filter-item">分类
          <select id="ba-filter-category" class="filter-select">
            <option value=""${draft.category === "" ? " selected" : ""}>全部</option>
            ${CATEGORIES.map((category) => `<option value="${category}"${draft.category === category ? " selected" : ""}>${category}</option>`).join("")}
          </select>
        </label>
        <label class="filter-item ba-filter-item">设计任务
          <input id="ba-filter-design-task" class="filter-input" value="${escapeHtml(draft.design_task_id)}" placeholder="design_task_id">
        </label>
        <label class="filter-item ba-filter-item">生成请求
          <input id="ba-filter-generation-request" class="filter-input" value="${escapeHtml(draft.generation_request_id)}" placeholder="generation_request_id">
        </label>
      </div>
      <div class="ba-filter-actions">
        <button id="ba-apply-filter" class="btn btn-primary btn-sm">应用筛选</button>
        <button id="ba-clear-filter" class="btn btn-secondary btn-sm">清空</button>
        <button id="ba-refresh" class="btn btn-secondary btn-sm${state.flags.refreshing ? " btn-loading" : ""}">
          <i data-lucide="refresh-cw"></i>刷新
        </button>
      </div>
    </div>
  `;
}

function renderTable(rows) {
  const selectableRows = rows.filter((r) => ["queued", "failed", "lost"].includes(r.status));
  const allSelectableSelected = selectableRows.length > 0
    && selectableRows.every((r) => state.selection.has(r.id));
  return `
    <div class="table-container ba-table-wrap">
      <table class="table ba-table">
        <thead>
          <tr>
            <th class="ba-select-col">
              <input type="checkbox" id="ba-select-all"
                ${selectableRows.length === 0 ? "disabled" : ""}
                ${allSelectableSelected ? "checked" : ""}
                title="全选当前页面上 queued/failed/lost 状态的题目">
            </th>
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
                ${["queued", "failed", "lost"].includes(attempt.status)
                  ? `<input type="checkbox" class="ba-row-select" data-build-attempt-id="${escapeHtml(attempt.id)}" ${state.selection.has(attempt.id) ? "checked" : ""}>`
                  : `<span class="ba-muted" title="仅 queued/failed/lost 状态可选">—</span>`}
              </td>
              <td>
                <div class="ba-title">${escapeHtml(attempt.title || attempt.challenge_id || attempt.id)}</div>
                ${failureSummary(attempt) ? `<div class="ba-failure-line">${escapeHtml(failureSummary(attempt))}</div>` : ""}
              </td>
              <td>${softPill(categoryLabel(attempt.category))}</td>
              <td>${escapeHtml(attempt.difficulty || "-")}</td>
              <td>${buildStatusIndicator(attempt.status)}</td>
              <td>${artifactPill(attempt.artifact_status)}</td>
              <td>${renderProgressCell(attempt)}</td>
              <td>${escapeHtml(attempt.worker || "-")}</td>
              <td>${escapeHtml(executionCountLabel(attempt))}</td>
              <td class="table-cell-time">${escapeHtml(formatDateTime(attempt.created_at))}</td>
              <td>
                <div class="btn-group">
                  <button class="btn btn-secondary btn-xs ba-open-detail">详情</button>
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

function renderAttemptCards(rows) {
  return `
    <div class="ba-card-list">
      ${rows.map((attempt) => `
        <article class="ba-attempt-card" data-build-attempt-id="${escapeHtml(attempt.id)}">
          <div class="ba-card-head">
            <label class="ba-card-select">
              ${["queued", "failed", "lost"].includes(attempt.status)
                ? `<input type="checkbox" class="ba-row-select" data-build-attempt-id="${escapeHtml(attempt.id)}" ${state.selection.has(attempt.id) ? "checked" : ""}>`
                : `<span class="ba-muted">—</span>`}
            </label>
            <div class="ba-card-title">
              <strong>${escapeHtml(attempt.title || attempt.challenge_id || attempt.id)}</strong>
              <span>${escapeHtml(shortId(attempt.id))} · ${escapeHtml(executionCountLabel(attempt))}</span>
            </div>
          </div>
          <div class="ba-card-badges">
            ${softPill(categoryLabel(attempt.category))}
            ${buildStatusIndicator(attempt.status)}
            ${artifactPill(attempt.artifact_status)}
          </div>
          ${failureSummary(attempt) ? `<div class="ba-card-failure">${escapeHtml(failureSummary(attempt))}</div>` : ""}
          <dl class="ba-card-meta">
            <div><dt>难度</dt><dd>${escapeHtml(attempt.difficulty || "-")}</dd></div>
            <div><dt>进度</dt><dd>${renderProgressCell(attempt)}</dd></div>
            <div><dt>Worker</dt><dd>${escapeHtml(attempt.worker || "-")}</dd></div>
            <div><dt>创建</dt><dd>${escapeHtml(formatDateTime(attempt.created_at))}</dd></div>
          </dl>
          <div class="ba-card-actions">
            <button class="btn btn-primary btn-sm ba-open-detail">详情</button>
            <button class="btn btn-danger btn-sm ba-delete" title="删除"><i data-lucide="trash-2"></i>删除</button>
          </div>
        </article>
      `).join("")}
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
  const tone = buildDetailTone(attempt.status);
  root.innerHTML = `
    ${renderBuildReadinessWarning()}
    <div class="ba-detail-toolbar">
      <button class="btn btn-ghost" id="ba-back">
        <i data-lucide="arrow-left"></i> 返回列表
      </button>
      <div class="btn-group ba-detail-actions">
        <button id="ba-refresh" class="btn btn-secondary btn-sm">
          <i data-lucide="refresh-cw"></i>刷新
        </button>
        ${attempt.status === "queued" && buildProfileReady(attempt.category)
          ? `<button id="ba-worker" class="btn btn-primary btn-sm"><i data-lucide="play"></i>运行</button>`
          : ""}
        ${attempt.status === "running"
          ? `<button id="ba-stop-worker" class="btn btn-danger btn-sm"><i data-lucide="square"></i>结束</button>`
          : ""}
        ${attempt.status === "failed"
          ? `<button class="btn btn-secondary btn-sm ba-revalidate" data-build-attempt-id="${escapeHtml(attempt.id)}"><i data-lucide="shield-check"></i>重新校验</button>`
          : ""}
        ${attempt.status === "failed" && buildProfileReady(attempt.category)
          ? `<button class="btn btn-primary btn-sm ba-repair" data-build-attempt-id="${escapeHtml(attempt.id)}"><i data-lucide="wrench"></i>分析并修复</button>`
          : ""}
        ${(attempt.status === "failed" || attempt.status === "lost") && buildProfileReady(attempt.category)
          ? `<button class="btn btn-primary btn-sm ba-retry" data-build-attempt-id="${escapeHtml(attempt.id)}"><i data-lucide="rotate-cw"></i>重试构建</button>`
          : ""}
        ${(attempt.status === "failed" || attempt.status === "lost") && buildProfileReady(attempt.category)
          ? `<button class="btn btn-secondary btn-sm ba-clean-rebuild" data-build-attempt-id="${escapeHtml(attempt.id)}"><i data-lucide="eraser"></i>干净重建</button>`
          : ""}
        ${["failed", "lost", "succeeded"].includes(attempt.status)
          ? `<button class="btn btn-danger btn-sm ba-delete" data-build-attempt-id="${escapeHtml(attempt.id)}">
              <i data-lucide="trash-2"></i>删除
            </button>`
          : ""}
      </div>
    </div>

    <section class="ba-detail-hero ba-detail-${tone}" data-build-attempt-id="${escapeHtml(attempt.id)}">
      <div class="ba-detail-mainline">
        <div class="ba-detail-badges">
          ${buildStatusIndicator(attempt.status)}
          ${artifactPill(attempt.artifact_status)}
          ${softPill(executionCountLabel(attempt))}
          ${attempt.category ? softPill(categoryLabel(attempt.category)) : ""}
        </div>
        <h2>${escapeHtml(designTaskLabel(attempt))} · ${escapeHtml(executionCountLabel(attempt))}</h2>
        <div class="ba-detail-meta">
          <span>关联设计</span>
          <span title="${escapeHtml(attempt.shard_basename)}">队列 ${escapeHtml(shortShardName(attempt.shard_basename))}</span>
          <span>${escapeHtml(formatDateTime(attempt.created_at))}</span>
        </div>
      </div>
      ${renderDetailFailure(attempt)}
    </section>

    <section class="card ba-info-card">
      <div class="card-header">
        <div><div class="card-title">运行信息</div><div class="card-subtitle">构建记录与文件证据的后端状态。</div></div>
      </div>
      <dl class="ba-info-grid">
        <div><dt>关联设计</dt><dd>${designTaskLink(attempt)}</dd></div>
        <div><dt>文件队列</dt><dd>${fileQueueToken(attempt.shard_basename)}</dd></div>
        <div><dt>worker</dt><dd>${escapeHtml(attempt.worker || "-")}</dd></div>
        <div><dt>执行轮次</dt><dd>${escapeHtml(executionCountLabel(attempt))}</dd></div>
        <div><dt>Hermes 超时</dt><dd>${escapeHtml(timeoutLabel(attempt))}</dd></div>
        <div><dt>开始时间</dt><dd>${escapeHtml(formatDateTime(attempt.started_at))}</dd></div>
        <div><dt>完成时间</dt><dd>${escapeHtml(formatDateTime(attempt.finished_at))}</dd></div>
        <div><dt>产物目录</dt><dd class="mono">${escapeHtml(attempt.resulting_challenge_dir || "-")}</dd></div>
      </dl>
    </section>

    <section class="card ba-section-card">
      <div class="card-header">
        <div><div class="card-title">执行历史</div></div>
        <span class="pill">${(attempt.executions || []).length}</span>
      </div>
      ${renderExecutions(attempt.executions || [])}
    </section>

    <section class="card ba-section-card">
      <div class="card-header">
        <div><div class="card-title">AI 修复记录</div></div>
        <span class="pill">${(attempt.repair_runs || []).length}</span>
      </div>
      ${renderRepairRuns(attempt.repair_runs || [])}
    </section>

    <section class="card ba-section-card">
      <div class="card-header">
        <div><div class="card-title">进度事件</div></div>
        <span class="pill" id="ba-progress-event-count">${(attempt.progress_events || []).length}</span>
      </div>
      ${renderProgressEvents(attempt.progress_events || [])}
    </section>
  `;
}

function buildDetailTone(status) {
  if (status === "succeeded") return "success";
  if (status === "running") return "info";
  if (status === "queued") return "warning";
  if (status === "failed" || status === "lost") return "danger";
  return "neutral";
}

function renderBuildReadinessWarning() {
  const readiness = appState.data?.build_readiness;
  if (!readiness || readiness.ready) return "";
  const unavailable = Object.values(readiness.categories || {}).filter((item) => !item.ready);
  if (!unavailable.length) return "";
  const missing = unavailable.filter((item) => item.reason === "missing_profile" || !item.reason);
  const blocked = unavailable.filter((item) => item.reason && item.reason !== "missing_profile");
  return `
    <div class="dt-readiness-banner" role="alert">
      <i data-lucide="triangle-alert"></i>
      <div>
        <strong>构建环境未就绪，相关任务暂时无法构建</strong>
        ${missing.length ? `<span>缺少 Hermes Profile：${missing.map((item) => `<code>${escapeHtml(item.profile)}</code>`).join("、")}</span>` : ""}
        ${missing.length ? `<span>请先运行：<code>${escapeHtml(missing.map((item) => item.create_command).join(" ; "))}</code></span>` : ""}
        ${blocked.map((item) => `<span><code>${escapeHtml(item.profile)}</code>：${escapeHtml(item.message || "Profile 配置未就绪")}</span>`).join("")}
      </div>
    </div>
  `;
}

function buildProfileReady(category) {
  const readiness = appState.data?.build_readiness;
  if (!readiness || readiness.ready) return true;
  return readiness.categories?.[category]?.ready === true;
}

function renderExecutions(rows) {
  if (!rows.length) return `<div class="empty card-body">没有执行历史</div>`;
  const sortedRows = [...rows].sort((a, b) => Number(b.iteration_no || 0) - Number(a.iteration_no || 0));
  return `
    <div class="table-container">
      <table class="table">
        <thead><tr><th>轮次</th><th>状态</th><th>类型</th><th>Worker</th><th>开始</th><th>完成</th><th>结果</th></tr></thead>
        <tbody>
          ${sortedRows.map((row) => `
            <tr>
              <td>#${escapeHtml(String(row.iteration_no || "-"))}</td>
              <td>${buildStatusIndicator(row.status)}</td>
              <td>${escapeHtml(row.execution_kind || "-")}</td>
              <td>${escapeHtml(row.worker_id || "-")}</td>
              <td class="table-cell-time">${escapeHtml(formatDateTime(row.started_at || row.created_at))}</td>
              <td class="table-cell-time">${escapeHtml(formatDateTime(row.finished_at))}</td>
              <td>${escapeHtml(row.error || row.exit_class || "-")}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderRepairRuns(runs) {
  if (!runs.length) return `<div class="empty card-body">没有 AI 修复记录</div>`;
  return `
    <div class="ba-events">
      ${runs.map((run) => `
        <div class="ba-event mono">
          ${escapeHtml(run.repair_id || "-")}
          ${escapeHtml(run.phase || "unknown")}/${escapeHtml(run.status || "unknown")}
          ${escapeHtml(run.message || "")}
          ${run.log_path ? `<div>log: ${escapeHtml(run.log_path)}</div>` : ""}
        </div>
      `).join("")}
    </div>
  `;
}

function renderProgressEvents(events) {
  return `
    <div id="ba-progress-events" class="ba-events">
      ${events.length
        ? events.map(renderProgressEvent).join("")
        : `<div class="empty" data-empty-events>没有进度事件</div>`}
    </div>
  `;
}

function renderProgressEvent(event) {
  const tone = event.message?.startsWith("carry-forward:") ? "warning" : "normal";
  return `
    <div class="ba-event ba-event-${tone} mono" data-progress-event-id="${event.id}">
      #${event.id} ${escapeHtml(event.stage)}/${escapeHtml(event.status)}
      ${event.challenge_id ? escapeHtml(event.challenge_id) : "shard"}
      ${escapeHtml(event.message || "")}
    </div>
  `;
}

function detailWithoutEvents(detail) {
  if (!detail) return null;
  const { progress_events: _events, ...rest } = detail;
  return rest;
}

function patchDetailEvents(nextDetail) {
  if (!state.detail || state.detail.id !== nextDetail.id) return false;
  if (JSON.stringify(detailWithoutEvents(state.detail)) !== JSON.stringify(detailWithoutEvents(nextDetail))) return false;
  const current = state.detail.progress_events || [];
  const next = nextDetail.progress_events || [];
  if (next.length < current.length) return false;
  for (let index = 0; index < current.length; index += 1) {
    if (JSON.stringify(current[index]) !== JSON.stringify(next[index])) return false;
  }
  if (next.length === current.length) return true;
  const container = document.querySelector("#ba-progress-events");
  if (!container) return false;
  const appended = next.slice(current.length);
  const knownIds = new Set(current.map((event) => event.id));
  const lastKnownId = current.length ? current[current.length - 1].id : null;
  if ((lastKnownId !== null && appended[0].id <= lastKnownId)
      || appended.some((event, index) => knownIds.has(event.id)
      || (index > 0 && appended[index - 1].id >= event.id))) return false;
  container.querySelector("[data-empty-events]")?.remove();
  container.insertAdjacentHTML("beforeend", appended.map(renderProgressEvent).join(""));
  for (const event of appended) {
    const node = container.querySelector(`[data-progress-event-id="${event.id}"]`);
    if (node) detailEventNodes.set(event.id, node);
  }
  const count = document.querySelector("#ba-progress-event-count");
  if (count) count.textContent = String(next.length);
  return true;
}

function rebuildDetailEventNodes() {
  detailEventNodes.clear();
  document.querySelectorAll("#ba-progress-events [data-progress-event-id]").forEach((node) => {
    detailEventNodes.set(Number(node.dataset.progressEventId), node);
  });
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
    unknown: "未生成",
  }[value] || "未生成";
}

function failureSummary(attempt) {
  if (!["failed", "lost"].includes(String(attempt.status || ""))) return "";
  const structured = validationFailureSummary(attempt);
  if (structured) return structured;
  if (attempt.failure_summary) return attempt.failure_summary;
  // Short-circuit on the newest validate/complete terminal — if it's passed
  // (e.g. a successful revalidate), there is no failure to display even if
  // older failed events are still in the timeline.
  const events = attempt.progress_events || [];
  for (const event of [...events].reverse()) {
    if (event.stage !== "validate" && event.stage !== "complete") continue;
    if (event.status !== "passed" && event.status !== "failed") continue;
    if (event.status === "passed") return "";
    const reason = failureMessageReason(event.message || "") || "未知原因";
    return event.stage === "validate"
      ? `校验失败：${reason}`
      : `构建执行失败：${reason}`;
  }
  if (attempt.error === "shard execution failed") return "构建执行失败";
  return attempt.error || "";
}

function validationFailureSummary(attempt) {
  const failureClass = String(attempt.validation_failure_class || "").trim();
  if (!failureClass) return "";
  const parts = [VALIDATION_FAILURE_LABELS[failureClass] || failureClass];
  const detail = latestValidationDetail(attempt);
  const detailCode = String(detail?.code || "").trim();
  if (detailCode) parts.push(VALIDATION_DETAIL_LABELS[detailCode] || detailCode);
  const status = String(attempt.validation_status || "").trim();
  if (status && status !== detailCode) parts.push(status);
  return parts.join(" · ");
}

function latestValidationDetail(attempt) {
  const details = Array.isArray(attempt.validation_failure_details)
    ? attempt.validation_failure_details
    : [];
  return details.find((item) => item && typeof item === "object") || null;
}

function validationFailureEvidence(attempt) {
  const detail = latestValidationDetail(attempt);
  const chunks = [];
  const hint = String(detail?.hint || "").trim();
  if (hint) chunks.push(hint);
  const signature = String(attempt.validation_failure_signature || "").trim();
  const prompt = signature.match(/prompt=([^|]+)/)?.[1];
  if (prompt) chunks.push(`等待提示 ${prompt}`);
  const stderr = String(attempt.validation_stderr_tail || "").trim();
  if (stderr) chunks.push(stderr.replace(/\s+/g, " ").slice(0, 220));
  return chunks.find(Boolean) || "";
}

function renderDetailFailure(attempt) {
  const summary = failureSummary(attempt);
  if (!summary) return "";
  const evidence = validationFailureEvidence(attempt);
  return `
    <div class="ba-detail-failure">
      <strong>失败原因</strong>
      <span>
        ${escapeHtml(summary)}
        ${evidence ? `<small>${escapeHtml(evidence)}</small>` : ""}
      </span>
    </div>
  `;
}

function failureMessageReason(message) {
  if (message.includes("error=")) {
    return message.split("error=", 2)[1].replace(/^[\s;,]+|[\s;,]+$/g, "");
  }
  return message.trim();
}

function applyFiltersFromInputs() {
  clearFilterInteraction();
  state.filterDraft = readFilterInputs();
  state.filters = { ...state.filterDraft };
  state.detailId = null;
  state.detail = null;
  state.list = null;
  state.selection.clear();
  state.lanePools = null;
  render(appState.data);
}

function clearFilters() {
  clearFilterInteraction();
  state.filters = { ...EMPTY_FILTERS };
  syncFilterDraft();
  state.detailId = null;
  state.detail = null;
  state.list = null;
  state.selection.clear();
  state.lanePools = null;
  render(appState.data);
}

function openDetail(id) {
  detailEventNodes.clear();
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
    if (event.target.closest("#ba-start-queue")) {
      startCurrentQueue();
      return;
    }
    if (event.target.closest("#ba-start-selected")) {
      startSelectedQueue();
      return;
    }
    if (event.target.closest("#ba-start-selected-lanes")) {
      startSelectedLanes();
      return;
    }
    if (event.target.closest("#ba-retry-selected-lanes")) {
      retrySelectedLanes();
      return;
    }
    if (event.target.closest("#ba-worker")) {
      startBuildWorker();
      return;
    }
    if (event.target.closest("#ba-stop-worker")) {
      stopBuildWorker();
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
    if (event.target.closest(".ba-repair") && attemptId) {
      repairAttempt(attemptId);
      return;
    }
    if (event.target.closest(".ba-clean-rebuild") && attemptId) {
      cleanRebuildAttempt(attemptId);
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
      clearFilterInteraction();
      applyFiltersFromInputs();
      return;
    }
    if (event.target.id === "ba-select-all") {
      toggleSelectAll(event.target.checked);
      return;
    }
    if (event.target.id === "ba-lane-count") {
      currentLaneCount();
      render(appState.data);
      return;
    }
    if (event.target.classList.contains("ba-row-select")) {
      toggleRowSelection(event.target.dataset.buildAttemptId, event.target.checked);
    }
  });

  document.addEventListener("input", (event) => {
    const root = document.querySelector('[data-view="build-attempts"]');
    if (!root || !root.contains(event.target)) return;
    if (
      event.target.id === "ba-filter-worker"
      || event.target.id === "ba-filter-design-task"
      || event.target.id === "ba-filter-generation-request"
    ) {
      markFilterInteraction();
      updateFilterDraftFromInputs();
      scheduleFilterApply();
    }
  });

  document.addEventListener("focusin", (event) => {
    const root = document.querySelector('[data-view="build-attempts"]');
    if (!root || !root.contains(event.target)) return;
    if (isFilterControl(event.target)) markFilterInteraction();
  });

  document.addEventListener("pointerdown", (event) => {
    const root = document.querySelector('[data-view="build-attempts"]');
    if (!root || !root.contains(event.target)) return;
    if (isFilterControl(event.target)) markFilterInteraction();
  });
}

export function activate() {
  state.filterDraft = { ...state.filters };
  if (Object.values(state.filters).every((value) => !value)) {
    state.filters = { ...EMPTY_FILTERS };
    state.filterDraft = { ...EMPTY_FILTERS };
  }
}
