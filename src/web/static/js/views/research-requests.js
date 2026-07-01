import { api, del, postJson } from "../api.js";
import { initIcons } from "../ui/icons.js";
import { showToast } from "../ui/toast.js";
import { confirmDeletion } from "../ui/delete-dialog.js";
import { confirmBackfill } from "../ui/backfill-dialog.js";
import {
  escapeHtml,
  categoryLabel,
  categoryTone,
  formatDateTime,
  failureMeta,
  requestStatusPill,
  requestStatusMeta,
  runStatusLabel,
  difficultyLabel,
  researchErrorMessage,
  statusIndicator,
  softPill,
} from "../ui/format.js";
import { setView } from "../router.js";
import { showDesignTasksForRequest } from "./design-tasks.js";
import { showRunsForRequest } from "./research-runs.js";

const REQUEST_STATUSES = ["draft", "queued", "researching", "researched", "failed"];
const ACTIVE_POLL_MS = 2000;
const SETTLED_POLL_MS = 12000;

const state = {
  requests: null,
  categories: null,
  detail: null,
  detailId: null,
  worker: null,
  flags: {},
  filter: { category: "", displayStatus: "" },
  detailPoll: { timer: null, loading: false },
  lastRender: null,
};

function listSignature() {
  return [
    "L",
    JSON.stringify(state.requests),
    state.categories?.length ?? -1,
    state.filter.category,
    state.filter.displayStatus,
    state.flags.requests?.loading || false,
    state.flags.requests?.error || "",
    state.flags.deleting || false,
  ].join("|");
}

function detailSignature() {
  return [
    "D",
    state.detailId,
    JSON.stringify(state.detail),
    state.worker?.running || false,
    state.worker?.available !== false,
    state.flags.detail?.loading || false,
    state.flags.detail?.refreshing || false,
    state.flags.deleting || false,
    state.flags.backfill?.loading || false,
  ].join("|");
}

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
  if (state.filter.displayStatus) p.set("display_status", state.filter.displayStatus);
  return p.toString() ? `/api/research/requests?${p}` : "/api/research/requests";
}

async function fetchDetail(id) {
  if (state.detail !== null) return;
  state.flags.detail = { loading: true };
  try {
    state.detail = await api(`/api/research/requests/${id}`);
    state.flags.detail = { loading: false };
    render(state.data);
    initIcons();
  } catch (err) {
    showToast(researchErrorMessage(err.message), true);
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
  return (
    state.worker?.running ||
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
  if (state.flags.deleting) {
    scheduleDetailPoll(ACTIVE_POLL_MS);
    return;
  }

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
    initIcons();
  } catch (err) {
    showToast(researchErrorMessage(err.message), true);
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
    showToast("设计任务已生成");
    await reloadDetail();
    showDesignTasksForRequest(state.detailId);
    initIcons();
  } catch (err) {
    showToast(researchErrorMessage(err.message), true);
  }
}

async function requestBackfillPreview(runId) {
  return requestBackfill(runId, { apply: false });
}

async function requestBackfillApply(runId, preview) {
  return requestBackfill(runId, {
    apply: true,
    expected_log_sha256: preview.log_sha256,
  });
}

async function requestBackfill(runId, body) {
  const response = await fetch(`/api/research/runs/${encodeURIComponent(runId)}/backfill`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  let payload = {};
  try { payload = await response.json(); } catch { /* keep empty payload */ }
  if (!response.ok) {
    const error = new Error(payload.detail || payload.message || `请求失败 (${response.status})`);
    error.code = payload.code || "request_failed";
    error.detail = payload.detail || error.message;
    throw error;
  }
  return payload;
}

function backfillErrorMessage(err) {
  if (err?.code === "preview_stale") return "日志已变化，请重新预览后再恢复";
  return err?.detail || err?.message || "日志恢复失败";
}

async function backfillLatestRun(runId) {
  if (state.flags.backfill?.loading) return;
  state.flags.backfill = { loading: true };
  render(state.data);
  initIcons();
  try {
    let preview;
    try {
      preview = await requestBackfillPreview(runId);
    } catch (err) {
      await confirmBackfill({ preview: null, error: err });
      return;
    }
    const confirmed = await confirmBackfill({ preview });
    if (!confirmed) return;
    await requestBackfillApply(runId, preview);
    showToast("研究结果已从日志恢复");
    await refreshDetail();
  } catch (err) {
    showToast(backfillErrorMessage(err), true);
  } finally {
    state.flags.backfill = { loading: false };
    render(state.data);
    initIcons();
  }
}

async function deleteRequest(requestId) {
  if (state.flags.deleting) return;
  state.flags.deleting = true;
  render(state.data);
  initIcons();
  try {
    const choice = await confirmDeletion({
      title: "删除研究需求",
      message: "将同时删除研究记录、设计任务、题目设计和构建记录。你可以选择是否一并删除产物文件。",
    });
    if (choice === null) return;
    const query = choice ? "?delete_artifacts=true" : "?delete_artifacts=false";
    const result = await del(`/api/research/requests/${requestId}${query}`);
    showToast(result.warnings?.length ? result.warnings[0] : "研究需求已删除");
    state.detailId = null;
    state.detail = null;
    state.requests = null;
    await ensureRequests();
    render(state.data);
    initIcons();
  } catch (err) {
    showToast(err.message, true);
  } finally {
    state.flags.deleting = false;
    render(state.data);
    initIcons();
  }
}

async function runWorkerAction(action, body = {}, requestId = null) {
  try {
    const endpoint = requestId && action === "start"
      ? `/api/research/requests/${encodeURIComponent(requestId)}/worker/start`
      : `/api/research/worker/${action}`;
    const result = await postJson(endpoint, body);
    showToast(result.message || "OK");
    state.worker = result.state || null;
    await refreshDetail({ startPolling: true });
  } catch (err) {
    showToast(researchErrorMessage(err.message), true);
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
    state.lastRender = null;
    return;
  }

  if (state.detailId) {
    renderDetail(root);
    return;
  }

  const sig = listSignature();
  if (sig === state.lastRender) return;
  state.lastRender = sig;

  const flag = state.flags.requests || {};
  if (flag.loading && !state.requests) {
    root.innerHTML = `<div class="empty">正在加载研究需求…</div>`;
    return;
  }
  if (flag.error) {
    root.innerHTML = `
      <div class="rq-alert rq-alert-error">
        <div class="rq-alert-title">研究需求加载失败</div>
        <p>${escapeHtml(flag.error)}</p>
      </div>
    `;
    return;
  }

  const items = state.requests || [];
  const cats = state.categories || [];

  root.innerHTML = `
    <div class="rq-page-header">
      <div>
        <h2 class="rq-page-title">研究需求</h2>
        <p class="rq-page-desc">管理研究意图、执行状态和设计任务生成。</p>
      </div>
      <div class="rq-page-actions">
        <button class="btn btn-secondary" id="req-refresh"${state.flags.requests?.loading ? " disabled" : ""}>
          <i data-lucide="refresh-cw"></i> 刷新
        </button>
        <button class="btn btn-primary" data-jump="research-submit">
          <i data-lucide="plus"></i> 新建需求
        </button>
      </div>
    </div>
    <section class="card rq-list-card">
      <div class="rq-list-summary">
        <div>
          <div class="card-title">需求列表</div>
          <div class="card-subtitle">打开需求可查看研究过程、质量检查和设计任务。</div>
        </div>
        <span class="pill">共 ${items.length} 条</span>
      </div>
      <div class="filter-bar">
        <label class="filter-item">题目类别
          <select id="req-filter-cat" class="filter-select">
            <option value=""${state.filter.category === "" ? " selected" : ""}>全部类别</option>
            ${cats.map(c => `<option value="${escapeHtml(c.code)}"${state.filter.category === c.code ? " selected" : ""}>${escapeHtml(c.display_name || categoryLabel(c.code))}</option>`).join("")}
          </select>
        </label>
        <label class="filter-item">执行状态
          <select id="req-filter-status" class="filter-select">
            <option value=""${state.filter.displayStatus === "" ? " selected" : ""}>全部状态</option>
            ${REQUEST_STATUSES.map(s => `<option value="${escapeHtml(s)}"${state.filter.displayStatus === s ? " selected" : ""}>${escapeHtml(requestStatusMeta[s].label)}</option>`).join("")}
          </select>
        </label>
        <button id="req-clear-filter" class="filter-clear">重置筛选</button>
      </div>
      ${items.length ? renderRequestsTable(items) : `<div class="empty card-body">没有符合条件的研究需求</div>`}
    </section>
  `;
}

function renderRequestsTable(items) {
  return `
    <div class="table-container rq-table-wrap">
      <table class="table rq-table">
        <thead>
          <tr>
            <th>研究主题</th>
            <th>类别</th>
            <th>目标配置</th>
            <th>当前阶段</th>
            <th>创建时间</th>
            <th><span class="sr-only">操作</span></th>
          </tr>
        </thead>
        <tbody>
          ${items.map(r => `
            <tr class="table-row-clickable" data-id="${escapeHtml(r.id)}">
              <td>
                <div class="rq-topic">${escapeHtml(r.topic)}</div>
                <div class="rq-short-id">${escapeHtml(r.id.slice(0, 8))}</div>
              </td>
              <td>${softPill(categoryLabel(r.category), categoryTone(r.category))}</td>
              <td>${renderTargetSummary(r)}</td>
              <td>${requestStatusPill(r.display_status || r.status)}</td>
              <td class="table-cell-time">${escapeHtml(formatDateTime(r.created_at))}</td>
              <td class="rq-row-action">
                <button class="btn btn-danger btn-xs req-delete" title="删除需求"><i data-lucide="trash-2"></i></button>
                <button class="btn btn-ghost btn-xs" title="查看详情"><i data-lucide="chevron-right"></i></button>
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderTargetSummary(request) {
  const distribution = Object.entries(request.difficulty_distribution || {})
    .filter(([, count]) => Number(count) > 0)
    .map(([difficulty, count]) => `${difficultyLabel(difficulty)} ${count}`)
    .join(" / ");
  return `
    <div class="rq-target-count">${escapeHtml(request.target_count)} 道</div>
    <div class="rq-target-dist">${escapeHtml(distribution || "未配置难度")}</div>
  `;
}

function renderDetail(root) {
  if (state.detail === null) {
    fetchDetail(state.detailId);
    ensureWorker();
    const loadingSig = "D-loading|" + state.detailId;
    if (state.lastRender !== loadingSig) {
      state.lastRender = loadingSig;
      root.innerHTML = `<div class="empty">正在加载需求详情…</div>`;
    }
    return;
  }

  const sig = detailSignature();
  if (sig === state.lastRender) {
    scheduleDetailPoll(detailNeedsActivePolling() ? ACTIVE_POLL_MS : SETTLED_POLL_MS);
    return;
  }
  state.lastRender = sig;

  const {
    request,
    latest_run: latest,
    runs = [],
    sources = [],
    findings_by_kind = {},
    technique_family_report: techniqueFamilyReport = null,
    design_tasks_summary = null,
  } = state.detail;
  const worker = state.worker || {};
  const workerRunning = !!worker.running;
  const available = worker.available !== false;
  const findingCount = Object.values(findings_by_kind).reduce((sum, items) => sum + items.length, 0);
  const minimumFindings = Number(request.minimum_findings || Math.ceil(Number(request.target_count || 0) * 0.5));
  const qualityPassed = request.status === "researched" && findingCount >= minimumFindings;
  const designTaskCount = design_tasks_summary?.total || 0;
  const displayStatus = request.display_status || request.status;
  const heroState = requestHeroState(displayStatus, qualityPassed);

  root.innerHTML = `
    <button class="btn btn-ghost rq-back" id="research-back">
      <i data-lucide="arrow-left"></i> 返回研究需求
    </button>

    <section class="rq-hero rq-hero-${heroState.tone}" aria-label="${escapeHtml(heroState.label)}">
      <div class="rq-hero-content">
        <div class="rq-badges rq-hero-badges">
          ${renderHeroCategory(request.category)}
          <span class="rq-hero-status rq-status-${escapeHtml(displayStatus)}">
            <span class="rq-status-dot" aria-hidden="true"></span>
            ${escapeHtml(heroState.label)}
          </span>
          <span class="rq-hero-id" title="需求 ID: ${escapeHtml(request.id)}">${escapeHtml(request.id.slice(0, 8))}</span>
        </div>
        <h2>${escapeHtml(request.topic)}</h2>
        <div class="rq-hero-meta">
          <span>${escapeHtml(request.target_count)} 道</span>
          <span aria-hidden="true">·</span>
          <span>${escapeHtml(renderDifficultySummary(request.difficulty_distribution))}</span>
          <span aria-hidden="true">·</span>
          <span>${escapeHtml(formatShortDate(request.created_at))}</span>
        </div>
      </div>
      <div class="rq-hero-actions">
        ${renderPrimaryAction({ request, latest, qualityPassed, designTaskCount, workerRunning, available })}
        <button class="btn btn-ghost btn-icon btn-icon-sm" id="detail-refresh" aria-label="刷新"${state.flags.detail?.refreshing ? " disabled" : ""}>
          <i data-lucide="refresh-cw"></i>
        </button>
      </div>
    </section>

    <div class="rq-detail-layout">
      <div class="rq-detail-main">
        ${renderResearchProgress(request, latest, findingCount, minimumFindings)}
        ${renderRequestConfiguration(request)}
        ${renderTechniqueFamilyReport(techniqueFamilyReport)}
        ${renderFindings(findings_by_kind, minimumFindings)}
        ${renderSources(sources)}
        ${renderDesignTasksSummary(design_tasks_summary, request, qualityPassed, findingCount, minimumFindings)}
        <section class="card rq-section-card">
          <div class="card-header">
            <div>
              <div class="card-title">运行历史</div>
              <div class="card-subtitle">默认显示最近三次研究执行记录。</div>
            </div>
            <button class="btn btn-ghost btn-sm detail-open-runs"><i data-lucide="list"></i> 查看全部</button>
          </div>
          ${runs.length ? renderRunsTable(runs.slice(0, 3)) : `<div class="empty card-body">暂无运行记录</div>`}
        </section>
      </div>
      <aside class="rq-detail-side">
        ${renderExecutionSummary({ request, latest, worker, findingCount, minimumFindings, sources, designTaskCount })}
        <section class="card rq-side-actions">
          <div class="rq-side-title">相关操作</div>
          <button class="btn btn-secondary btn-sm" id="detail-run-loop"${workerRunning || !available ? " disabled" : ""}>
            <i data-lucide="rotate-cw"></i> 持续处理该需求
          </button>
          <button class="btn btn-secondary btn-sm" id="detail-run-stop"${!workerRunning || !available ? " disabled" : ""}>
            <i data-lucide="pause"></i> 暂停 Worker
          </button>
          <button class="btn btn-ghost btn-sm" id="detail-open-logs"><i data-lucide="file-text"></i> 查看研究日志</button>
          <button class="btn btn-danger btn-sm" id="detail-delete-request"><i data-lucide="trash-2"></i> 删除研究需求</button>
          ${workerRunning ? `<p class="rq-side-note">暂停 Worker 可能影响正在执行的其他研究需求。</p>` : ""}
        </section>
      </aside>
    </div>
  `;

  scheduleDetailPoll(detailNeedsActivePolling() ? ACTIVE_POLL_MS : SETTLED_POLL_MS);
}

function requestHeroState(displayStatus, qualityPassed) {
  if (displayStatus === "researched" && qualityPassed) {
    return { tone: "success", label: "研究结果已就绪" };
  }
  if (displayStatus === "researched") {
    return { tone: "warning", label: "研究结果需要处理" };
  }
  if (displayStatus === "failed") {
    return { tone: "danger", label: "研究执行失败" };
  }
  if (displayStatus === "researching") {
    return { tone: "info", label: "研究正在执行" };
  }
  if (displayStatus === "queued") {
    return { tone: "warning", label: "研究等待执行" };
  }
  return { tone: "neutral", label: "研究需求草稿" };
}

function renderHeroCategory(category) {
  const meta = {
    web: { label: "Web", icon: "globe-2" },
    pwn: { label: "Pwn", icon: "bug" },
    re: { label: "RE", icon: "binary" },
  }[category] || { label: categoryLabel(category), icon: "shapes" };
  const tone = ["web", "pwn", "re"].includes(category) ? category : "other";
  return `<span class="rq-hero-chip rq-category-chip rq-category-${tone}"><i data-lucide="${meta.icon}"></i>${escapeHtml(meta.label)}</span>`;
}

function renderDifficultySummary(distribution) {
  const entries = Object.entries(distribution || {}).filter(([, count]) => Number(count) > 0);
  if (!entries.length) return "未配置难度";
  if (entries.length === 1) return difficultyLabel(entries[0][0]);
  return entries.map(([difficulty, count]) => `${difficultyLabel(difficulty)} ${count}`).join(" / ");
}

function formatShortDate(value) {
  if (!value) return "日期未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date).replaceAll("/", "-");
}

function renderPrimaryAction({ request, latest, qualityPassed, designTaskCount, workerRunning, available }) {
  if (designTaskCount > 0) {
    return `<button class="btn btn-primary design-tasks-view"><i data-lucide="workflow"></i> 查看设计任务</button>`;
  }
  if (qualityPassed) {
    return `<button class="btn btn-primary design-tasks-generate"><i data-lucide="wand-sparkles"></i> 生成设计任务</button>`;
  }
  if (request.status === "researched") {
    return `<button class="btn btn-primary" id="detail-run-once"${workerRunning || !available ? " disabled" : ""}><i data-lucide="plus-circle"></i> 补充研究</button>`;
  }
  if (latest && (latest.status === "queued" || latest.status === "running")) {
    return `<button class="btn btn-primary detail-open-runs"><i data-lucide="activity"></i> 查看运行状态</button>`;
  }
  if (request.status === "failed" && latest && Number(latest.attempt) >= Number(request.max_attempts)) {
    return `<button class="btn btn-secondary" disabled><i data-lucide="circle-x"></i> 已达重试上限</button>`;
  }
  const label = request.status === "failed" ? "重新研究" : "启动研究";
  return `<button class="btn btn-primary" id="detail-run-once"${workerRunning || !available ? " disabled" : ""}><i data-lucide="play"></i> ${label}</button>`;
}

function renderResearchProgress(request, latest, findingCount, minimumFindings) {
  const displayStatus = request.display_status || request.status;
  const stageIndex = displayStatus === "researched" ? 3 : displayStatus === "researching" ? 1 : displayStatus === "queued" ? 0 : -1;
  const stages = ["等待研究", "Agent 研究", "质量检查", "研究完成"];
  return `
    <section class="card rq-section-card">
      <div class="card-header">
        <div><div class="card-title">研究进度</div><div class="card-subtitle">从需求排队到通过最小质量检查。</div></div>
        ${latest ? `<span class="pill">第 ${escapeHtml(latest.attempt)} 次运行</span>` : ""}
      </div>
      <div class="card-body">
        <div class="rq-progress-steps">
          ${stages.map((stage, index) => `<div class="rq-progress-step ${index < stageIndex || displayStatus === "researched" ? "done" : index === stageIndex ? "active" : ""}"><span>${index < stageIndex || displayStatus === "researched" ? "✓" : index + 1}</span><strong>${stage}</strong></div>`).join("")}
        </div>
        ${latest?.last_error ? renderFailureAlert(latest) : ""}
        ${request.status === "researched" ? `<div class="rq-quality ${findingCount >= minimumFindings ? "passed" : "failed"}"><i data-lucide="${findingCount >= minimumFindings ? "badge-check" : "triangle-alert"}"></i><div><strong>${findingCount >= minimumFindings ? "已通过质量检查" : "未通过质量检查"}</strong><p>有效研究结论 ${findingCount} 条，最低要求 ${minimumFindings} 条。${findingCount >= minimumFindings ? "" : "可继续补充研究。"}</p></div></div>` : ""}
      </div>
    </section>
  `;
}

function renderRequestConfiguration(request) {
  const constraints = Object.entries(request.runtime_constraints || {});
  const seeds = request.seed_urls || [];
  return `
    <section class="card rq-section-card">
      <div class="card-header"><div><div class="card-title">需求配置</div><div class="card-subtitle">提交时记录的研究意图与执行约束。</div></div></div>
      <dl class="rq-config-grid">
        <div><dt>题目类别</dt><dd>${escapeHtml(categoryLabel(request.category))}</dd></div>
        <div><dt>目标数量</dt><dd>${escapeHtml(request.target_count)} 道</dd></div>
        <div><dt>最大尝试次数</dt><dd>${escapeHtml(request.max_attempts)} 次</dd></div>
        <div><dt>创建时间</dt><dd>${escapeHtml(formatDateTime(request.created_at))}</dd></div>
        <div class="rq-config-wide"><dt>难度分布</dt><dd class="rq-chip-list">${Object.entries(request.difficulty_distribution || {}).map(([key, value]) => `<span>${escapeHtml(difficultyLabel(key))} ${escapeHtml(value)}</span>`).join("") || "未配置"}</dd></div>
        <div class="rq-config-wide"><dt>运行约束</dt><dd class="rq-chip-list">${constraints.map(([key, value]) => `<span>${escapeHtml(key)}：${escapeHtml(typeof value === "object" ? JSON.stringify(value) : value)}</span>`).join("") || "未设置运行约束"}</dd></div>
        <div class="rq-config-wide"><dt>种子 URL</dt><dd>${seeds.length ? seeds.map(url => `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(url)}</a>`).join("") : "未提供种子 URL"}</dd></div>
      </dl>
    </section>
  `;
}

function renderFailureAlert(run) {
  const meta = failureMeta(run.last_error_category);
  const title = run.last_error_title || "本次运行未完成";
  const description = run.last_error_description || "研究运行未完成。";
  const actions = Array.isArray(run.last_error_actions) ? run.last_error_actions : [];
  const backfillLoading = state.flags.backfill?.loading;
  return `
    <div class="rq-alert rq-alert-error rq-alert-${escapeHtml(meta.tone)} rq-progress-alert">
      <div class="rq-alert-heading">
        <i data-lucide="${escapeHtml(meta.icon)}"></i>
        <div class="rq-alert-title">${escapeHtml(title)}</div>
      </div>
      <p>${escapeHtml(description)}</p>
      ${actions.length ? `
        <div class="rq-alert-actions">
          <ul>
            ${actions.map(action => `<li>${escapeHtml(action)}</li>`).join("")}
          </ul>
        </div>
      ` : ""}
      ${run.recoverable === true ? `
        <div class="rq-alert-backfill">
          <button class="btn btn-secondary btn-sm rq-backfill-run" data-run-id="${escapeHtml(run.id)}"${backfillLoading ? " disabled" : ""}>
            <i data-lucide="database-backup"></i> ${backfillLoading ? "正在恢复…" : "尝试从日志恢复结果"}
          </button>
        </div>
      ` : ""}
      <details class="rq-alert-details">
        <summary>原始错误</summary>
        <code>${escapeHtml(run.last_error || "")}</code>
      </details>
    </div>
  `;
}

function renderRunsTable(runs) {
  return `
    <div class="table-container">
      <table class="table">
        <thead>
          <tr>
            <th>次数</th>
            <th>状态</th>
            <th class="rq-history-failure-col">失败原因</th>
            <th>执行 Worker</th>
            <th>开始时间</th>
            <th>结束时间</th>
          </tr>
        </thead>
        <tbody>
          ${runs.map(run => `
            <tr>
              <td class="table-cell-id">第 ${escapeHtml(run.attempt)} 次</td>
              <td>${statusIndicator(run.status)}</td>
              <td class="rq-history-failure-col">${run.status === "failed" ? escapeHtml(run.last_error_title || "") : ""}</td>
              <td class="table-cell-mono">${escapeHtml(run.claimed_by || "—")}</td>
              <td class="table-cell-time">${escapeHtml(formatDateTime(run.started_at))}</td>
              <td class="table-cell-time">${escapeHtml(formatDateTime(run.finished_at))}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderDesignTasksSummary(summary, request, qualityPassed, findingCount, minimumFindings) {
  const total = summary?.total || 0;
  const counts = summary?.by_status || {};
  const summaryEntries = Object.entries(counts)
    .filter(([, n]) => n > 0)
    .map(([status, n]) => `${runStatusLabel(status)} ${n}`)
    .join(" · ");
  const blocker = request?.status !== "researched"
    ? "研究完成并通过质量检查后才能生成设计任务。"
    : !qualityPassed
      ? `当前有 ${findingCount} 条有效结论，最低需要 ${minimumFindings} 条。`
      : "研究质量已达标，可以生成题目设计任务。";
  return `
    <section class="card rq-section-card">
      <div class="card-header">
        <div>
          <div class="card-title">设计任务</div>
          <div class="card-subtitle">${escapeHtml(summaryEntries || "尚未生成设计任务")}</div>
        </div>
        <div class="btn-group">
          <button class="btn btn-secondary btn-sm design-tasks-view"${total ? "" : " disabled"}>
            <i data-lucide="list"></i> 查看设计任务
          </button>
          <button class="btn btn-primary btn-sm design-tasks-generate"${qualityPassed && !total ? "" : " disabled"}>
            <i data-lucide="wand-sparkles"></i> 生成设计任务
          </button>
          <span class="pill">${total}</span>
        </div>
      </div>
      <div class="rq-section-message ${qualityPassed ? "ready" : ""}">${total ? "设计任务已生成，可进入题目设计页面查看详情。" : blocker}</div>
    </section>
  `;
}

function renderFindings(findingsByKind, minimumFindings) {
  const entries = Object.entries(findingsByKind || {});
  if (!entries.length) return "";
  const count = entries.reduce((sum, [, items]) => sum + items.length, 0);
  return `
    <section class="card rq-section-card">
      <div class="card-header">
        <div><div class="card-title">研究结论</div><div class="card-subtitle">已获得 ${count} 条，质量门最低要求 ${minimumFindings} 条。</div></div>
        <span class="pill">${count} 条</span>
      </div>
      <div class="rq-findings">
        ${entries.map(([kind, items]) => `
          <div class="rq-finding-group">
            <div class="rq-finding-kind">${escapeHtml(findingKindLabel(kind))} · ${items.length}</div>
            <div class="rq-finding-list">
              ${items.map((item) => `
                <div class="rq-finding-item">
                  <div class="rq-finding-label">${escapeHtml(item.label)}</div>
                  ${item.technique_family ? `<div class="rq-chip-list"><span>${escapeHtml(techniqueFamilyLabel(item.technique_family))}</span></div>` : ""}
                  <div class="rq-finding-summary">${escapeHtml(item.summary)}</div>
                </div>
              `).join("")}
            </div>
          </div>
        `).join("")}
      </div>
    </section>
  `;
}

function renderTechniqueFamilyReport(report) {
  const distribution = report?.distribution || {};
  const entries = Object.entries(distribution).filter(([, count]) => Number(count) > 0);
  if (!entries.length) return "";
  const otherRatio = Number(report.other_ratio || 0);
  const warnRatio = Number(report.other_warn_ratio ?? 0.3);
  const isWarn = Array.isArray(report.warnings) && report.warnings.includes("classification_miss_rate_high");
  return `
    <section class="card rq-section-card">
      <div class="card-header">
        <div>
          <div class="card-title">技术族分布</div>
          <div class="card-subtitle">按后端归一化结果统计 research findings。</div>
        </div>
        <span class="pill ${isWarn ? "rq-status-failed" : ""}">Other ${Math.round(otherRatio * 100)}%</span>
      </div>
      <div class="rq-chip-list">
        ${entries.map(([family, count]) => `<span>${escapeHtml(techniqueFamilyLabel(family))} ${escapeHtml(count)}</span>`).join("")}
      </div>
      ${isWarn ? `<div class="rq-section-message">分类 miss-rate 偏高（阈值 ${Math.round(warnRatio * 100)}%），请检查 lane vocabulary 或研究范围。</div>` : ""}
    </section>
  `;
}

function renderSources(sources) {
  if (!sources.length) return "";
  return `
    <section class="card rq-section-card">
      <div class="card-header">
        <div><div class="card-title">参考来源</div><div class="card-subtitle">研究 Agent 使用的外部资料。</div></div>
        <span class="pill">${sources.length} 条</span>
      </div>
      <div class="rq-sources">
        ${sources.map(s => `
          <div class="rq-source-item">
            <div class="rq-source-head">
              <a href="${escapeHtml(s.url)}" target="_blank" rel="noreferrer">${escapeHtml(s.title || sourceHost(s.url))}</a>
              <span>${escapeHtml(sourceHost(s.url))}</span>
              <i data-lucide="external-link"></i>
            </div>
            <p>${escapeHtml(s.summary || "暂无来源摘要")}</p>
          </div>
        `).join("")}
      </div>
    </section>
  `;
}

function renderExecutionSummary({ request, latest, worker, findingCount, minimumFindings, sources, designTaskCount }) {
  const workerStatus = worker.running ? "running" : "idle";
  return `
    <section class="card rq-execution-summary">
      <div class="rq-side-title">执行摘要</div>
      <dl>
        <div><dt>当前状态</dt><dd>${requestStatusPill(request.display_status || request.status)}</dd></div>
        <div><dt>持久化状态</dt><dd>${requestStatusPill(request.status)}</dd></div>
        <div><dt>最新运行</dt><dd>${latest ? `第 ${escapeHtml(latest.attempt)} 次 · ${escapeHtml(runStatusLabel(latest.status))}` : "暂无"}</dd></div>
        <div><dt>Worker</dt><dd>${statusIndicator(workerStatus)}</dd></div>
        <div><dt>研究结论</dt><dd class="${findingCount >= minimumFindings ? "rq-value-ok" : ""}">${findingCount} / ${minimumFindings} 条</dd></div>
        <div><dt>参考来源</dt><dd>${sources.length} 条</dd></div>
        <div><dt>设计任务</dt><dd>${designTaskCount} 个</dd></div>
      </dl>
    </section>
  `;
}

function findingKindLabel(kind) {
  return ({ technique: "技术要点", variant: "变化方式", pitfall: "常见陷阱", reference: "参考信息" })[kind] || kind;
}

function techniqueFamilyLabel(family) {
  return ({
    auth: "Auth",
    injection: "Injection",
    server_side: "Server-side",
    client_side: "Client-side",
    upload: "Upload/export",
    node_api: "Node/API",
    stack: "Stack",
    format_string: "Format string",
    heap: "Heap",
    integer_oob: "Integer/OOB",
    sandbox: "Sandbox",
    kernel: "Kernel",
    crackme: "Crackme",
    vm_bytecode: "VM/bytecode",
    runtime: "Runtime",
    language: "Language",
    platform: "Platform",
    visual_game: "Visual/game",
    other: "Other",
  })[family] || family;
}

function sourceHost(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

export function bind() {
  document.addEventListener("ctf:open-research-request", (event) => {
    const requestId = event.detail?.requestId;
    if (!requestId) return;
    state.detailId = requestId;
    state.detail = null;
    setView("research-requests");
    render(state.data);
    initIcons();
  });

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
      initIcons();
      return;
    }
    if (e.target.closest("#detail-run-once")) {
      runWorkerAction("start", { kind: "once", max_jobs: 1 }, state.detailId);
      return;
    }
    if (e.target.closest("#detail-run-loop")) {
      runWorkerAction("start", { kind: "loop", max_jobs: 1 }, state.detailId);
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
        initIcons();
      });
      return;
    }
    if (e.target.closest(".detail-open-runs")) {
      showRunsForRequest(state.detailId);
      return;
    }
    if (e.target.closest("#detail-open-logs") || e.target.closest(".detail-open-logs")) {
      setView("research-logs");
      return;
    }
    if (e.target.closest("#detail-delete-request") && state.detailId) {
      deleteRequest(state.detailId);
      return;
    }
    const backfillButton = e.target.closest(".rq-backfill-run");
    if (backfillButton?.dataset.runId) {
      backfillLatestRun(backfillButton.dataset.runId);
      return;
    }
    if (e.target.closest("#req-clear-filter")) {
      state.filter = { category: "", displayStatus: "" };
      forceReloadRequests();
      return;
    }
    if (e.target.closest("#req-refresh")) {
      forceReloadRequests().finally(initIcons);
      return;
    }
    if (e.target.closest(".design-tasks-generate")) {
      generateDesignTasks();
      return;
    }
    if (e.target.closest(".design-tasks-view")) {
      showDesignTasksForRequest(state.detailId);
      return;
    }

    const row = e.target.closest(".table-row-clickable");
    if (row) {
      if (e.target.closest(".req-delete")) {
        deleteRequest(row.dataset.id);
        return;
      }
      state.detailId = row.dataset.id;
      state.detail = null;
      render(state.data);
      initIcons();
    }
  });

  document.addEventListener("change", (e) => {
    const root = document.querySelector('[data-view="research-requests"]');
    if (!root || !root.contains(e.target)) return;

    if (e.target.id === "req-filter-cat") {
      state.filter.category = e.target.value;
      forceReloadRequests();
    } else if (e.target.id === "req-filter-status") {
      state.filter.displayStatus = e.target.value;
      forceReloadRequests();
    }
  });
}
