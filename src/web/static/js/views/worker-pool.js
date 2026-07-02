import { api } from "../api.js";
import { escapeHtml, formatDateTime, statusIndicator } from "../ui/format.js";

const LIMIT = 120;
const ACTIVE_STATUSES = new Set(["queued", "running"]);
const TERMINAL_STATUSES = new Set(["succeeded", "failed", "lost"]);

let cached = {
  attempts: [],
  lanes: [],
  loading: false,
  error: "",
};

function compareUpdatedDesc(a, b) {
  return String(b.updated_at || b.created_at || "").localeCompare(String(a.updated_at || a.created_at || ""));
}

function compareAttemptDesc(a, b) {
  return String(b.created_at || "").localeCompare(String(a.created_at || ""));
}

function attemptKey(item) {
  return String(item.design_task_id || item.challenge_id || item.id || "");
}

function attemptLabel(item) {
  const task = item.task_no ? `第 ${item.task_no} 题` : "";
  const title = item.title || item.challenge_id || item.id || "-";
  return task ? `${task} · ${title}` : title;
}

function attemptSubLabel(item) {
  return [
    item.category || "",
    item.difficulty || "",
    item.worker || "",
    item.shard_basename || "",
  ].filter(Boolean).join(" · ");
}

function clampPercent(value) {
  const numeric = Number(value || 0);
  if (!Number.isFinite(numeric)) return 0;
  return Math.max(0, Math.min(100, Math.round(numeric)));
}

function statusTone(status) {
  if (status === "succeeded") return "ok";
  if (status === "failed" || status === "lost") return "bad";
  if (status === "running") return "active";
  if (status === "queued") return "wait";
  return "idle";
}

function statusText(status) {
  if (status === "succeeded") return "已完成";
  if (status === "failed") return "失败";
  if (status === "lost") return "已丢失";
  return status || "未知";
}

function activeDetail(item) {
  if (item.status === "queued") return "等待 Worker 领取";
  if (item.message) return item.message;
  return item.worker ? "Worker 正在处理" : "构建进行中";
}

function terminalDetail(item) {
  if (item.status === "succeeded") return "构建与校验已通过";
  return item.failure_summary || item.error || item.message || "未提供失败原因";
}

function normalizeAttempts(rows) {
  const grouped = new Map();
  for (const item of rows || []) {
    const key = attemptKey(item);
    if (!key) continue;
    const current = grouped.get(key);
    if (!current || compareAttemptDesc(item, current) < 0) {
      grouped.set(key, item);
    }
  }
  return [...grouped.values()].sort(compareUpdatedDesc);
}

async function refreshData() {
  if (cached.loading) return;
  cached.loading = true;
  cached.error = "";
  try {
    const [list, poolResult] = await Promise.all([
      api(`/api/build-attempts?limit=${LIMIT}`),
      api("/api/build-attempts/worker/pools"),
    ]);
    cached.attempts = normalizeAttempts(Array.isArray(list) ? list : []);
    cached.lanes = Array.isArray(poolResult.pools) ? poolResult.pools : [];
  } catch (err) {
    cached.error = err.message;
    cached.attempts = [];
    cached.lanes = [];
  } finally {
    cached.loading = false;
  }
}

function summaryStats() {
  return {
    total: cached.attempts.length,
    active: cached.attempts.filter((item) => ACTIVE_STATUSES.has(item.status)).length,
    succeeded: cached.attempts.filter((item) => item.status === "succeeded").length,
    failed: cached.attempts.filter((item) => item.status === "failed").length,
    lost: cached.attempts.filter((item) => item.status === "lost").length,
    runningPools: cached.lanes.filter((pool) => pool.running).length,
  };
}

function renderMetric(label, value, icon) {
  return `
    <div class="rp-metric">
      <i data-lucide="${icon}"></i>
      <span>${escapeHtml(label)}</span>
      <strong>${value}</strong>
    </div>
  `;
}

function renderHero() {
  const stats = summaryStats();
  return `
    <section class="rp-hero">
      <div class="rp-hero-main">
        <div>
          <span class="rp-eyebrow">实时进度</span>
          <h2>构建任务总览</h2>
        </div>
        <p>按设计任务汇总当前构建态，运行、结果、队列池分区展示。</p>
      </div>
      <div class="rp-hero-metrics">
        ${renderMetric("总任务", stats.total, "layout-grid")}
        ${renderMetric("运行中", stats.active, "activity")}
        ${renderMetric("已完成", stats.succeeded, "check-circle-2")}
        ${renderMetric("失败 / 丢失", stats.failed + stats.lost, "triangle-alert")}
        ${renderMetric("活跃队列", stats.runningPools, "git-branch")}
      </div>
    </section>
  `;
}

function renderProgressLine(item) {
  const percent = clampPercent(item.percent);
  const failed = item.status === "failed" || item.status === "lost";
  return `
    <div class="rp-progress-line">
      <div class="rp-progress-track">
        <i class="${failed ? "failed" : ""}" style="width:${percent}%"></i>
      </div>
      <span>${percent}%</span>
    </div>
  `;
}

function renderActiveCard(item) {
  return `
    <article class="rp-active-card">
      <div class="rp-active-main">
        <div class="rp-task-title">
          <strong>${escapeHtml(attemptLabel(item))}</strong>
          <span>${escapeHtml(attemptSubLabel(item) || item.shard_basename || "-")}</span>
        </div>
        <div class="rp-status-block">
          ${statusIndicator(item.status)}
          <time>${escapeHtml(formatDateTime(item.updated_at || item.created_at))}</time>
        </div>
      </div>
      ${renderProgressLine(item)}
      <div class="rp-active-foot">
        <span>${escapeHtml(activeDetail(item))}</span>
        <span>${escapeHtml(item.worker || "未分配 Worker")}</span>
      </div>
    </article>
  `;
}

function renderProgressPanel() {
  const activeRows = cached.attempts.filter((item) => ACTIVE_STATUSES.has(item.status));
  return `
    <section class="rp-panel rp-active-panel">
      <div class="rp-panel-head">
        <div>
          <h3>正在运行</h3>
          <p>只显示等待执行和执行中的任务。</p>
        </div>
      </div>
      <div class="rp-active-grid">
        ${activeRows.length ? activeRows.map(renderActiveCard).join("") : `<div class="rp-empty">暂无进行中的构建</div>`}
      </div>
    </section>
  `;
}

function renderLanePool(pool) {
  const laneCount = Array.isArray(pool.lanes) ? pool.lanes.length : 0;
  return `
    <article class="rp-pool-card">
      <div class="rp-lane-top">
        <div>
          <strong>队列池 ${escapeHtml(pool.id || "-")}</strong>
          <span>${escapeHtml(formatDateTime(pool.started_at) || pool.started_at || "-")} · ${laneCount} 条 lane</span>
        </div>
        ${statusIndicator(pool.running ? "running" : "completed")}
      </div>
      <div class="rp-pool-stats">
        <span>活动 lane ${Number(pool.active_lanes || 0)}</span>
        <span>成功 ${Number(pool.succeeded_lanes || 0)}</span>
        <span>失败 ${Number(pool.failed_lanes || 0)}</span>
      </div>
    </article>
  `;
}

function renderPoolPanel() {
  const runningPools = cached.lanes.filter((pool) => pool.running);
  return `
    <section class="rp-panel">
      <div class="rp-panel-head">
        <div>
          <h3>当前队列池</h3>
          <p>只展示当前还活着的 lane pool。</p>
        </div>
      </div>
      <div class="rp-pool-list">
        ${runningPools.length ? runningPools.map(renderLanePool).join("") : `<div class="rp-empty compact">暂无运行中的队列池</div>`}
      </div>
    </section>
  `;
}

function renderResultRow(item) {
  return `
    <article class="rp-result-row">
      <span class="rp-result-mark ${statusTone(item.status)}"></span>
      <div class="rp-result-main">
        <div class="rp-result-title">
          <strong>${escapeHtml(attemptLabel(item))}</strong>
          <span>${escapeHtml(attemptSubLabel(item) || item.shard_basename || "-")}</span>
        </div>
        <p>${escapeHtml(terminalDetail(item))}</p>
      </div>
      <div class="rp-result-side">
        <span>${escapeHtml(statusText(item.status))}</span>
        <time>${escapeHtml(formatDateTime(item.updated_at || item.created_at))}</time>
      </div>
    </article>
  `;
}

function renderRecentPanel() {
  const rows = cached.attempts.filter((item) => TERMINAL_STATUSES.has(item.status)).slice(0, 8);
  return `
    <section class="rp-panel">
      <div class="rp-panel-head">
        <div>
          <h3>最近结果</h3>
          <p>按最新尝试后的终态展示。</p>
        </div>
      </div>
      <div class="rp-result-list">
        ${rows.length ? rows.map(renderResultRow).join("") : `<div class="rp-empty compact">暂无终态记录</div>`}
      </div>
    </section>
  `;
}

function renderError() {
  return cached.error ? `<div class="rp-warning">${escapeHtml(cached.error)}</div>` : "";
}

async function repaint() {
  const root = document.querySelector('[data-view="worker-pool"]');
  if (!root) return;
  root.innerHTML = `
    ${renderError()}
    ${renderHero()}
    <div class="rp-layout">
      <main class="rp-main">
        ${renderProgressPanel()}
        ${renderRecentPanel()}
      </main>
      <aside class="rp-side">
        ${renderPoolPanel()}
      </aside>
    </div>
  `;
}

export function render() {
  const root = document.querySelector('[data-view="worker-pool"]');
  if (!root) return;
  repaint();
  if (!cached.loading) {
    refreshData().then(repaint).catch(() => repaint());
  }
}

export function bind() {
  document.addEventListener("click", (event) => {
    const root = document.querySelector('[data-view="worker-pool"]');
    if (!root || !root.contains(event.target)) return;
    const refresh = event.target.closest("[data-rp-refresh]");
    if (refresh) {
      cached.attempts = [];
      cached.lanes = [];
      cached.error = "";
      render();
    }
  });
}
