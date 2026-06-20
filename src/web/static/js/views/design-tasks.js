import { api, del, postJson } from "../api.js";
import { setView } from "../router.js";
import { initIcons } from "../ui/icons.js";
import { showToast } from "../ui/toast.js";
import { confirmDeletion } from "../ui/delete-dialog.js";
import {
  categoryLabel,
  categoryTone,
  designErrorMessage,
  designTaskStage,
  designTaskStatusLabel,
  designTaskStatusMeta,
  designTaskStatusPill,
  difficultyLabel,
  escapeHtml,
  formatDateTime,
  runStatusLabel,
  statusIndicator,
  softPill,
} from "../ui/format.js";

const ACTIVE_POLL_MS = 2500;
const SETTLED_POLL_MS = 12000;
const STATUSES = [
  "draft",
  "queued",
  "designing",
  "designed",
  "failed",
  "archived",
  "building",
  "built",
  "build_failed",
];

const state = {
  data: null,
  list: null,
  detail: null,
  detailId: null,
  filters: { generation_request_id: "", status: "", category: "" },
  selected: new Set(),
  flags: {},
  poll: { timer: null, loading: false },
};

export function showDesignTasksForRequest(requestId) {
  state.filters = {
    ...state.filters,
    generation_request_id: requestId || "",
  };
  state.selected.clear();
  state.detailId = null;
  state.detail = null;
  state.list = null;
  setView("design-tasks");
}

export function showDesignTaskDetail(taskId) {
  state.detailId = taskId || null;
  state.detail = null;
  state.list = null;
  setView("design-tasks");
}

async function ensureList() {
  if (state.list !== null || state.flags.list?.loading) return;
  state.flags.list = { loading: true, error: null };
  try {
    state.list = await api(buildListUrl());
    pruneSelection();
    state.flags.list = { loading: false, error: null };
  } catch (err) {
    state.flags.list = { loading: false, error: err.message };
  }
  render(state.data);
  initIcons();
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
  initIcons();
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
  return rows.some((task) => (
    task.status === "queued"
    || task.status === "designing"
    || task.status === "building"
  ));
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
      state.detail = await api(`/api/design-tasks/${state.detailId}`);
    } else {
      state.list = await api(buildListUrl());
      pruneSelection();
    }
    render(state.data);
    initIcons();
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
    showToast(action === "queue" ? "题目已提交设计" : "题目设计任务已归档");
    if (state.detailId) {
      await reloadDetail();
    } else {
      await reloadList();
    }
  } catch (err) {
    showToast(designErrorMessage(err.message), true);
  }
}

async function designTaskNow(taskId) {
  if (!taskId) return;
  state.flags.designing = { ...(state.flags.designing || {}), [taskId]: true };
  render(state.data);
  initIcons();
  try {
    const result = await postJson(`/api/design-tasks/${taskId}/design`, {});
    const failed = result.attempt_status === "failed";
    showToast(result.error ? designErrorMessage(result.error) : (failed ? "题目设计失败" : "题目设计已完成"), failed);
    state.detailId = taskId;
    await reloadDetail();
  } catch (err) {
    showToast(designErrorMessage(err.message), true);
  } finally {
    state.flags.designing = { ...(state.flags.designing || {}), [taskId]: false };
    render(state.data);
    initIcons();
  }
}

async function buildTaskNow(taskId) {
  if (!taskId) return;
  state.flags.building = { ...(state.flags.building || {}), [taskId]: true };
  render(state.data);
  initIcons();
  try {
    const result = await postJson(`/api/design-tasks/${taskId}/build`, {});
    state.selected.delete(taskId);
    showToast(`已提交构建 · ${shortId(result.build_attempt_id)}`);
    if (state.detailId) {
      await reloadDetail();
    } else {
      await reloadList();
    }
  } catch (err) {
    showToast(err.message, true);
  } finally {
    state.flags.building = { ...(state.flags.building || {}), [taskId]: false };
    render(state.data);
    initIcons();
  }
}

async function buildSelectedTasks() {
  const ids = [...state.selected];
  if (!ids.length) return;
  state.flags.bulkBuild = true;
  render(state.data);
  initIcons();
  try {
    const selectedTasks = ids
      .map((id) => (state.list || []).find((task) => task.id === id))
      .filter(Boolean);
    const requestIds = new Set(selectedTasks.map((task) => task.generation_request_id));
    const result = await postJson("/api/design-tasks/build", { design_task_ids: ids });
    state.selected.clear();
    showToast(`已提交 ${result.build_attempt_ids.length} 个构建任务`);
    await reloadList();
    const suffix = requestIds.size === 1
      ? `?generation_request_id=${encodeURIComponent([...requestIds][0])}`
      : "";
    window.location.hash = `#/build-attempts${suffix}`;
  } catch (err) {
    showToast(err.message, true);
  } finally {
    state.flags.bulkBuild = false;
    render(state.data);
    initIcons();
  }
}

async function deleteDesignTask(taskId) {
  if (state.flags.deleting) return;
  state.flags.deleting = true;
  render(state.data);
  initIcons();
  try {
    const choice = await confirmDeletion({
      title: "删除题目设计任务",
      message: "将同时删除设计历史和关联构建记录。你可以选择是否一并删除产物文件。",
    });
    if (choice === null) return;
    const query = choice ? "?delete_artifacts=true" : "?delete_artifacts=false";
    const result = await del(`/api/design-tasks/${taskId}${query}`);
    showToast(result.warnings?.length ? result.warnings[0] : "题目设计任务已删除");
    state.selected.delete(taskId);
    state.detailId = null;
    state.detail = null;
    state.list = null;
    await ensureList();
  } catch (err) {
    showToast(err.message, true);
  } finally {
    state.flags.deleting = false;
    render(state.data);
    initIcons();
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

  requestAnimationFrame(() => initIcons());
  schedulePoll(needsActivePolling() ? ACTIVE_POLL_MS : SETTLED_POLL_MS);
}

function renderList(root) {
  ensureList();
  const flag = state.flags.list || {};
  if (flag.loading && !state.list) {
    root.innerHTML = `<div class="empty">正在加载题目设计任务…</div>`;
    return;
  }
  if (flag.error) {
    root.innerHTML = `<div class="empty">${escapeHtml(flag.error)}</div>`;
    return;
  }
  const rows = state.list || [];
  const counts = summarizeTaskStages(rows);
  root.innerHTML = `
    <div class="dt-page-header">
      <div>
        <h2 class="dt-page-title">题目设计</h2>
        <p class="dt-page-desc">根据研究结论生成题目方案，并推进至构建阶段。</p>
      </div>
      <span class="pill">共 ${rows.length} 项</span>
    </div>

    ${state.filters.generation_request_id ? `
      <div class="dt-context-banner">
        <div><i data-lucide="link-2"></i><span>当前仅显示研究需求 <code>${escapeHtml(shortId(state.filters.generation_request_id))}</code> 下的设计任务</span></div>
        <div class="btn-group">
          <button class="btn btn-ghost btn-sm dt-open-request-context"><i data-lucide="arrow-up-right"></i>返回研究需求</button>
          <button class="btn btn-ghost btn-sm" id="dt-clear-request-filter">清除范围</button>
        </div>
      </div>
    ` : ""}

    <div class="dt-summary-grid">
      ${renderStageMetric("全部任务", rows.length, "layers-3", "neutral")}
      ${renderStageMetric("待设计", counts.pendingDesign, "sparkles", "warning")}
      ${renderStageMetric("可构建", counts.readyBuild, "hammer", "success")}
      ${renderStageMetric("失败", counts.failed, "triangle-alert", "danger")}
    </div>

    <section class="card dt-list-card">
      <div class="filter-bar filter-bar-vertical-sm dt-filters">
        <label class="filter-item">所属研究需求
          <input id="dt-filter-request" class="filter-input" value="${escapeHtml(state.filters.generation_request_id)}" placeholder="输入需求 ID">
        </label>
        <label class="filter-item">状态
          <select id="dt-filter-status" class="filter-select">
            <option value=""${state.filters.status === "" ? " selected" : ""}>全部状态</option>
            ${STATUSES.map((status) => `<option value="${escapeHtml(status)}"${state.filters.status === status ? " selected" : ""}>${escapeHtml(designTaskStatusLabel(status))}</option>`).join("")}
          </select>
        </label>
        <label class="filter-item">题目类别
          <select id="dt-filter-category" class="filter-select">
            <option value=""${state.filters.category === "" ? " selected" : ""}>全部类别</option>
            ${["web", "pwn", "re"].map((category) => `<option value="${category}"${state.filters.category === category ? " selected" : ""}>${escapeHtml(categoryLabel(category))}</option>`).join("")}
          </select>
        </label>
        <button id="dt-apply-filter" class="filter-clear">应用筛选</button>
        <button id="dt-clear-filter" class="filter-clear">重置</button>
      </div>
      ${rows.length ? renderTable(rows) : `<div class="empty card-body">没有符合条件的题目设计任务</div>`}
    </section>

    ${state.selected.size ? `
      <div class="dt-bulk-bar">
        <span>已选择 <strong>${state.selected.size}</strong> 个可构建任务</span>
        <div class="btn-group">
          <button class="btn btn-ghost btn-sm" id="dt-clear-selection">取消选择</button>
          <button id="dt-build-selected" class="btn btn-primary btn-sm${state.flags.bulkBuild ? " btn-loading" : ""}">
            <i data-lucide="hammer"></i> 批量构建
          </button>
        </div>
      </div>
    ` : ""}
  `;
}

function renderTable(rows) {
  return `
    <div class="table-container dt-table-wrap">
      <table class="table dt-table">
        <thead>
          <tr>
            <th class="dt-select-col"><span class="sr-only">选择</span></th>
            <th>题目</th>
            <th>类别与难度</th>
            <th>核心技术</th>
            <th>研究证据</th>
            <th>当前进度</th>
            <th><span class="sr-only">操作</span></th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((task) => `
            <tr data-design-task-id="${escapeHtml(task.id)}">
              <td>${renderBuildCheckbox(task)}</td>
              <td>
                <button class="dt-task-link dt-open-detail">${escapeHtml(task.title)}</button>
                <div class="dt-task-meta"><span class="mono">${escapeHtml(task.challenge_id)}</span><span>任务 ${escapeHtml(task.task_no)}</span></div>
              </td>
              <td><div class="dt-category-difficulty">${softPill(categoryLabel(task.category), categoryTone(task.category))}<span>${escapeHtml(difficultyLabel(task.difficulty))}</span></div></td>
              <td><div class="dt-technique" title="${escapeHtml(task.primary_technique)}">${escapeHtml(task.primary_technique || "未指定")}</div></td>
              <td><div class="dt-evidence-count"><strong>${(task.finding_ids || []).length}</strong><span>条引用</span></div></td>
              <td>${renderTaskProgress(task)}</td>
              <td class="dt-row-actions">${renderRowActions(task)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderBuildCheckbox(task) {
  if (!eligibleForBuild(task)) return "";
  return `
    <input
      class="dt-select-build"
      type="checkbox"
      title="选择用于批量构建"
      aria-label="选择 ${escapeHtml(task.title)}"
      ${state.selected.has(task.id) ? " checked" : ""}
    >
  `;
}

function renderRowActions(task) {
  if (task.status === "building" || task.status === "built") {
    return `
      <div class="btn-group design-task-actions">
        <button class="btn btn-primary btn-xs dt-open-builds"><i data-lucide="hammer"></i>${task.status === "built" ? "查看题目" : "查看构建"}</button>
        <button class="btn btn-ghost btn-xs dt-open-detail" title="查看详情"><i data-lucide="chevron-right"></i></button>
        <button class="btn btn-danger btn-xs dt-delete" title="删除">
          <i data-lucide="trash-2"></i>
        </button>
      </div>
    `;
  }
  const isBuilding = !!state.flags.building?.[task.id];
  const primary = task.status === "draft"
    ? `<button class="btn btn-primary btn-xs dt-queue"><i data-lucide="send"></i>提交设计</button>`
    : task.status === "queued"
      ? `<button class="btn btn-primary btn-xs dt-design"${state.flags.designing?.[task.id] ? " disabled" : ""}><i data-lucide="sparkles"></i>开始设计</button>`
      : eligibleForBuild(task)
        ? `<button class="btn btn-primary btn-xs dt-build${isBuilding ? " btn-loading" : ""}"${isBuilding ? " disabled" : ""}><i data-lucide="hammer"></i>${task.status === "build_failed" ? "重试构建" : "开始构建"}</button>`
        : `<button class="btn btn-secondary btn-xs dt-open-detail">查看详情</button>`;
  return `
    <div class="btn-group design-task-actions">
      ${primary}
      <button class="btn btn-ghost btn-xs dt-open-detail" title="查看详情"><i data-lucide="chevron-right"></i></button>
      <button class="btn btn-danger btn-xs dt-delete" title="删除">
        <i data-lucide="trash-2"></i>
      </button>
    </div>
  `;
}

function buildBadge(task) {
  if (!["building", "built", "build_failed"].includes(task.status)) return "";
  const label = task.status === "built"
    ? "构建完成"
    : task.status === "building"
      ? "构建中"
      : "构建失败";
  return `
    <button class="btn btn-secondary btn-xs dt-open-builds" title="查看构建记录">
      <i data-lucide="hammer"></i>${escapeHtml(label)}
    </button>
  `;
}

function renderDetail(root) {
  ensureDetail(state.detailId);
  const flag = state.flags.detail || {};
  if (flag.loading && !state.detail) {
    root.innerHTML = `<div class="empty">正在加载题目设计详情…</div>`;
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
  const isBuilding = !!state.flags.building?.[task.id];
  root.innerHTML = `
    <button class="btn btn-ghost dt-back" id="dt-back"><i data-lucide="arrow-left"></i>返回题目设计</button>

    <section class="dt-hero dt-hero-${designTaskStage(task.status)}" data-design-task-id="${escapeHtml(task.id)}">
      <div class="dt-hero-main">
        <div class="dt-hero-badges">
          ${softPill(categoryLabel(task.category), categoryTone(task.category))}
          ${designTaskStatusPill(task.status)}
          ${softPill(difficultyLabel(task.difficulty))}
        </div>
        <h2>${escapeHtml(task.title)}</h2>
        <div class="dt-hero-meta"><span class="mono">${escapeHtml(task.challenge_id)}</span><span>·</span><span>${escapeHtml(task.points)} 分</span>${task.port ? `<span>·</span><span>端口 ${escapeHtml(task.port)}</span>` : ""}<span>·</span><span>任务 ${escapeHtml(task.task_no)}</span></div>
      </div>
      <div class="dt-hero-actions">${renderDetailPrimaryAction(task, isDesigning, isBuilding)}</div>
    </section>

    <div class="dt-detail-layout">
      <div class="dt-detail-main">
        <section class="card dt-section-card">
          <div class="card-header"><div><div class="card-title">设计任务</div><div class="card-subtitle">题目的目标、场景和实现约束。</div></div></div>
          <div class="dt-brief-grid">
            <div class="dt-brief-wide"><span>学习目标</span><p>${escapeHtml(task.learning_objective || "未填写学习目标")}</p></div>
            <div><span>核心技术</span><strong>${escapeHtml(task.primary_technique || "未指定")}</strong></div>
            <div><span>端口</span><strong>${task.port ? escapeHtml(task.port) : "无需端口"}</strong></div>
            <div class="dt-brief-wide"><span>场景描述</span><p>${escapeHtml(task.scenario || "未填写场景描述")}</p></div>
            <div class="dt-brief-wide"><span>运行约束</span>${renderConstraintChips(task.constraints)}</div>
          </div>
        </section>

        <section class="card dt-section-card">
          <div class="card-header"><div><div class="card-title">研究依据</div><div class="card-subtitle">生成当前题目任务时引用的研究结论。</div></div><span class="pill">${(task.finding_ids || []).length} 条引用</span></div>
          <div class="dt-evidence">
            <p>${escapeHtml(task.evidence_summary || "暂无研究证据摘要")}</p>
            <button class="btn btn-secondary btn-sm dt-open-request" title="${escapeHtml(task.generation_request_id)}"><i data-lucide="arrow-up-right"></i>查看研究需求 · ${escapeHtml(shortId(task.generation_request_id))}</button>
          </div>
        </section>

        <section class="card dt-section-card">
          <div class="card-header"><div><div class="card-title">最新设计方案</div><div class="card-subtitle">结构化展示 Agent 生成的题目设计结果。</div></div>${latestDesign ? qualityGatePill(latestDesign.quality_gate_passed) : ""}</div>
          <div class="card-body">${latestDesign ? renderLatestDesign(latestDesign) : `<div class="empty">尚未生成题目设计方案</div>`}</div>
        </section>

        <section class="card dt-section-card">
          <div class="card-header"><div><div class="card-title">设计记录</div><div class="card-subtitle">Agent 的历次设计尝试与诊断信息。</div></div><span class="pill">${attempts.length} 次</span></div>
          ${attempts.length ? renderAttempts(attempts) : `<div class="empty card-body">暂无设计尝试</div>`}
        </section>
      </div>
      <aside class="dt-detail-side">
        ${renderTaskSummary(task, latestAttempt, latestDesign, attempts.length)}
        <section class="card dt-related-actions">
          <div class="dt-side-title">相关操作</div>
          ${buildBadge(task)}
          <button class="btn btn-secondary btn-sm dt-open-request"><i data-lucide="search"></i>查看研究需求</button>
          <button class="btn btn-ghost btn-sm dt-archive"${(task.status === "draft" || task.status === "queued") ? "" : " disabled"}><i data-lucide="archive"></i>归档任务</button>
          <button class="btn btn-danger btn-sm dt-delete"><i data-lucide="trash-2"></i>删除任务</button>
        </section>
      </aside>
    </div>
  `;
}

function renderAttempts(attempts) {
  return `
    <div class="table-container">
      <table class="table table-attempts-sm">
        <thead><tr><th>次数</th><th>状态</th><th>开始时间</th><th>结束时间</th><th>诊断</th><th>产物</th></tr></thead>
        <tbody>
          ${attempts.map((attempt) => `
            <tr>
              <td class="table-cell-id">第 ${attempt.attempt} 次</td>
              <td>${statusIndicator(attempt.status)}</td>
              <td class="table-cell-time">${escapeHtml(formatDateTime(attempt.started_at))}</td>
              <td class="table-cell-time">${escapeHtml(formatDateTime(attempt.finished_at))}</td>
              <td><div class="dt-attempt-error" title="${escapeHtml(attempt.last_error || "")}">${escapeHtml(attempt.last_error ? designErrorMessage(attempt.last_error) : "—")}</div></td>
              <td>
                <div class="btn-group">
                  ${attempt.prompt_artifact_url ? `<a class="btn btn-secondary btn-sm" href="${escapeHtml(attempt.prompt_artifact_url)}" target="_blank" rel="noopener">提示词</a>` : ""}
                  ${attempt.log_artifact_url ? `<a class="btn btn-secondary btn-sm" href="${escapeHtml(attempt.log_artifact_url)}" target="_blank" rel="noopener">日志</a>` : ""}
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
      <div class="dt-design-overview">
        <div><span>方案摘要</span><p>${escapeHtml(design.summary || "暂无设计摘要")}</p></div>
        <div><span>Flag 格式</span><code>${escapeHtml(design.flag_format || "未指定")}</code></div>
        <div><span>质量检查</span>${qualityGatePill(design.quality_gate_passed)}</div>
        ${design.validation_notes ? `<div class="dt-design-wide"><span>检查说明</span><p>${escapeHtml(design.validation_notes)}</p></div>` : ""}
      </div>
      <details class="design-json">
        <summary>开发者数据 · 原始 Payload</summary>
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
    passed ? "质量检查通过" : "质量检查未通过",
    passed ? "text-emerald-700 bg-emerald-50" : "text-rose-700 bg-rose-50",
  );
}

function summarizeTaskStages(rows) {
  return {
    pendingDesign: rows.filter((task) => ["draft", "queued", "designing"].includes(task.status)).length,
    readyBuild: rows.filter((task) => task.status === "designed").length,
    failed: rows.filter((task) => ["failed", "build_failed"].includes(task.status)).length,
  };
}

function renderStageMetric(label, value, icon, tone) {
  return `<div class="dt-metric dt-metric-${tone}"><i data-lucide="${icon}"></i><div><strong>${value}</strong><span>${label}</span></div></div>`;
}

function renderTaskProgress(task) {
  const stage = designTaskStage(task.status);
  const stageIndex = { plan: 0, design: 1, build: 2 }[stage] || 0;
  const failed = ["failed", "build_failed"].includes(task.status);
  return `
    <div class="dt-progress" title="${escapeHtml(designTaskStatusLabel(task.status))}">
      ${["规划", "设计", "构建"].map((label, index) => `<span class="${index < stageIndex ? "done" : index === stageIndex ? (failed ? "failed" : "active") : ""}"><i></i>${label}</span>`).join("")}
      ${designTaskStatusPill(task.status)}
    </div>
  `;
}

function renderDetailPrimaryAction(task, isDesigning, isBuilding) {
  if (task.status === "draft") {
    return `<button class="btn btn-primary dt-queue"><i data-lucide="send"></i>提交设计</button>`;
  }
  if (task.status === "queued") {
    return `<button class="btn btn-primary dt-design${isDesigning ? " btn-loading" : ""}"${isDesigning ? " disabled" : ""}><i data-lucide="sparkles"></i>开始设计</button>`;
  }
  if (task.status === "designing") {
    return `<button class="btn btn-secondary" disabled><i data-lucide="loader-circle"></i>设计执行中</button>`;
  }
  if (eligibleForBuild(task)) {
    return `<button class="btn btn-primary dt-build${isBuilding ? " btn-loading" : ""}"${isBuilding ? " disabled" : ""}><i data-lucide="hammer"></i>${task.status === "build_failed" ? "重试构建" : "开始构建"}</button>`;
  }
  if (["building", "built"].includes(task.status)) {
    return `<button class="btn btn-primary dt-open-builds"><i data-lucide="hammer"></i>${task.status === "built" ? "查看题目" : "查看构建"}</button>`;
  }
  return `<button class="btn btn-secondary" disabled>${escapeHtml(designTaskStatusLabel(task.status))}</button>`;
}

function renderConstraintChips(constraints) {
  const entries = Object.entries(constraints || {});
  if (!entries.length) return `<p>未设置额外约束</p>`;
  return `<div class="dt-chip-list">${entries.map(([key, value]) => `<span>${escapeHtml(key)}：${escapeHtml(typeof value === "object" ? JSON.stringify(value) : value)}</span>`).join("")}</div>`;
}

function renderTaskSummary(task, latestAttempt, latestDesign, attemptCount) {
  return `
    <section class="card dt-task-summary">
      <div class="dt-side-title">任务摘要</div>
      <dl>
        <div><dt>当前状态</dt><dd>${designTaskStatusPill(task.status)}</dd></div>
        <div><dt>所属需求</dt><dd class="mono">${escapeHtml(shortId(task.generation_request_id))}</dd></div>
        <div><dt>研究引用</dt><dd>${(task.finding_ids || []).length} 条</dd></div>
        <div><dt>设计尝试</dt><dd>${attemptCount} 次</dd></div>
        <div><dt>最近尝试</dt><dd>${latestAttempt ? escapeHtml(runStatusLabel(latestAttempt.status)) : "暂无"}</dd></div>
        <div><dt>质量检查</dt><dd>${latestDesign ? (latestDesign.quality_gate_passed ? "已通过" : "未通过") : "未执行"}</dd></div>
      </dl>
    </section>
  `;
}

function eligibleForBuild(task) {
  return task?.status === "designed" || task?.status === "build_failed";
}

function pruneSelection() {
  const eligibleIds = new Set((state.list || []).filter(eligibleForBuild).map((task) => task.id));
  for (const id of [...state.selected]) {
    if (!eligibleIds.has(id)) state.selected.delete(id);
  }
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
  state.selected.clear();
  state.list = null;
  render(state.data);
}

export function bind() {
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && isViewActive()) {
      schedulePoll(ACTIVE_POLL_MS);
    }
  });

  document.addEventListener("ctf:open-design-task", (event) => {
    const taskId = event.detail?.taskId;
    if (!taskId) return;
    state.detailId = taskId;
    state.detail = null;
    state.list = null;
    setView("design-tasks");
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
      state.selected.clear();
      state.list = null;
      render(state.data);
      return;
    }
    if (event.target.closest("#dt-clear-request-filter")) {
      state.filters.generation_request_id = "";
      state.selected.clear();
      state.list = null;
      render(state.data);
      return;
    }
    if (event.target.closest(".dt-open-request-context")) {
      openRequest(state.filters.generation_request_id);
      return;
    }
    if (event.target.closest("#dt-clear-selection")) {
      state.selected.clear();
      render(state.data);
      return;
    }
    if (event.target.closest("#dt-build-selected")) {
      buildSelectedTasks();
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
      return;
    }
    if (event.target.closest(".dt-build") && taskId) {
      buildTaskNow(taskId);
      return;
    }
    if (event.target.closest(".dt-delete") && taskId) {
      deleteDesignTask(taskId);
      return;
    }
    if (event.target.closest(".dt-open-builds") && taskId) {
      window.location.hash = `#/build-attempts?design_task_id=${encodeURIComponent(taskId)}`;
    }
  });

  document.addEventListener("change", (event) => {
    const root = document.querySelector('[data-view="design-tasks"]');
    if (!root || !root.contains(event.target)) return;
    if (event.target.id === "dt-filter-status") {
      applyFiltersFromInputs();
      return;
    }
    const checkbox = event.target.closest(".dt-select-build");
    if (!checkbox) return;
    const row = checkbox.closest("[data-design-task-id]");
    const taskId = row?.dataset.designTaskId;
    if (taskId && checkbox.checked) state.selected.add(taskId);
    if (taskId && !checkbox.checked) state.selected.delete(taskId);
    render(state.data);
  });
}
