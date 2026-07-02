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
        <span class="rp-eyebrow">实时进度</span>
        <h2>构建任务总览</h2>
        <p>按设计任务去重后的最新构建尝试，只保留最后态，不再把失败重试拆成多条任务。</p>
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

function renderProgressCard(item) {
  const isActive = ACTIVE_STATUSES.has(item.status);
  const isTerminal = TERMINAL_STATUSES.has(item.status);
  const detail = item.failure_summary || item.error || item.message || (isActive ? "进行中" : isTerminal ? "已结束" : "");
  return `
    <article class="rp-task-card">
      <div class="rp-task-head">
        <div>
          <strong>${escapeHtml(attemptLabel(item))}</strong>
          <span>${escapeHtml(attemptSubLabel(item))}</span>
        </div>
        ${statusIndicator(item.status)}
      </div>
      <div class="rp-progress-line">
        <div class="rp-progress-track">
          <i class="${item.status === "failed" || item.status === "lost" ? "failed" : ""}" style="width:${Number(item.percent || 0)}%"></i>
        </div>
        <span>${Number(item.percent || 0)}%</span>
      </div>
      <div class="rp-task-meta">
        <span>${escapeHtml(item.shard_basename || "-")}</span>
        <span>${escapeHtml(formatDateTime(item.updated_at || item.created_at))}</span>
      </div>
      <p>${escapeHtml(detail)}</p>
    </article>
  `;
}

function renderProgressPanel() {
  const activeRows = cached.attempts.filter((item) => ACTIVE_STATUSES.has(item.status));
  const recentRows = cached.attempts.slice(0, 8);
  return `
    <section class="rp-panel rp-active-panel">
      <div class="rp-panel-head">
        <div>
          <h3>最新构建</h3>
          <p>每个设计任务只显示最新一次尝试，重试不会重复计数。</p>
        </div>
      </div>
      <div class="rp-active-grid">
        ${activeRows.length ? activeRows.map(renderProgressCard).join("") : `<div class="rp-empty">暂无进行中的构建</div>`}
      </div>
      <div class="rp-lane-summary">
        ${recentRows.length ? recentRows.map(renderProgressCard).join("") : ""}
      </div>
    </section>
  `;
}

function renderLanePool(pool) {
  const laneCount = Array.isArray(pool.lanes) ? pool.lanes.length : 0;
  return `
    <article class="rp-lane-card">
      <div class="rp-lane-top">
        <div>
          <strong>队列池 ${escapeHtml(pool.id || "-")}</strong>
          <span>${escapeHtml(pool.started_at || "-")} · ${laneCount} 条 lane</span>
        </div>
        ${statusIndicator(pool.running ? "running" : "completed")}
      </div>
      <div class="rp-task-meta">
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
      <div class="rp-lane-summary">
        ${runningPools.length ? runningPools.map(renderLanePool).join("") : `<div class="rp-empty compact">暂无运行中的队列池</div>`}
      </div>
    </section>
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
        ${rows.length ? rows.map(renderProgressCard).join("") : `<div class="rp-empty compact">暂无终态记录</div>`}
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
        ${renderPoolPanel()}
        ${renderRecentPanel()}
      </main>
      <aside class="rp-panel rp-events">
        <div class="rp-panel-head">
          <div>
            <h3>实时说明</h3>
            <p>当前页用最新尝试推导任务状态，避免重复统计。</p>
          </div>
        </div>
        <div class="rp-event-list">
          <div class="rp-empty compact">当前只展示最新构建态与队列池，不再叠加历史事件。</div>
        </div>
      </aside>
    </div>
  `;
}

export function render() {
  const root = document.querySelector('[data-view="worker-pool"]');
  if (!root) return;
  repaint();
  if (!cached.loading && cached.attempts.length === 0 && !cached.error) {
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
