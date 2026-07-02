import {
  dotTone,
  escapeHtml,
  formatDateTime,
  runStatusLabel,
  stageLabel,
  statusIndicator,
} from "../ui/format.js";

const ACTIVE_STATUSES = new Set(["running", "queued"]);
const FINISHED_STATUSES = new Set(["passed", "done", "completed", "failed"]);

function clampPercent(value) {
  return Math.max(0, Math.min(100, Number(value || 0)));
}

function snapshotsOf(progress) {
  return Array.isArray(progress?.snapshots) ? progress.snapshots : [];
}

function eventsOf(progress) {
  return Array.isArray(progress?.events) ? progress.events : [];
}

function shortId(value) {
  return String(value || "").slice(0, 8) || "-";
}

function shortQueueName(value) {
  const name = String(value || "");
  if (!name) return "-";
  const match = name.match(/^([a-f0-9]{8})[a-f0-9-]*(?:\.iter-(\d+))?\.json$/i);
  if (match) return match[2] ? `${match[1]} · ${match[2]}` : match[1];
  return name.length > 28 ? `${name.slice(0, 18)}...${name.slice(-7)}` : name;
}

function itemTitle(item) {
  return item.challenge_id || shortQueueName(item.shard) || "-";
}

function itemSubTitle(item) {
  const queue = shortQueueName(item.shard);
  const worker = item.worker ? ` · ${item.worker}` : "";
  return `${stageLabel(item.stage)} · ${queue}${worker}`;
}

function activitySummary(proc, snapshots) {
  const lanePools = Array.isArray(proc.lane_pools) ? proc.lane_pools : [];
  const lanes = lanePools.flatMap((pool) => Array.isArray(pool.lanes) ? pool.lanes : []);
  return {
    running: snapshots.filter((item) => item.status === "running").length,
    queued: snapshots.filter((item) => item.status === "queued").length,
    failed: snapshots.filter((item) => item.status === "failed").length,
    finished: snapshots.filter((item) => FINISHED_STATUSES.has(item.status)).length,
    activeLanes: lanes.filter((lane) => lane.running).length,
    totalLanes: lanes.length,
    activePools: lanePools.filter((pool) => pool.running).length,
    totalPools: lanePools.length,
  };
}

function renderHero(proc, snapshots) {
  const summary = activitySummary(proc, snapshots);
  const running = Boolean(proc.running || summary.activeLanes || summary.running);
  const message = proc.message || (running ? "任务正在执行" : "暂无运行中的执行器");
  return `
    <section class="rp-hero">
      <div class="rp-hero-main">
        <span class="rp-eyebrow">实时进度</span>
        <h2>${running ? "执行中" : "当前空闲"}</h2>
        <p>${escapeHtml(message)}</p>
      </div>
      <div class="rp-hero-metrics">
        ${metricItem("运行任务", summary.running, "activity")}
        ${metricItem("等待执行", summary.queued, "clock")}
        ${metricItem("活动队列", summary.activeLanes || summary.activePools, "git-branch")}
        ${metricItem("失败记录", summary.failed, "triangle-alert")}
      </div>
    </section>
  `;
}

function metricItem(label, value, icon) {
  return `
    <div class="rp-metric">
      <i data-lucide="${icon}"></i>
      <span>${escapeHtml(label)}</span>
      <strong>${value}</strong>
    </div>
  `;
}

function renderActiveBoard(proc, snapshots, events) {
  const active = snapshots
    .filter((item) => ACTIVE_STATUSES.has(item.status))
    .sort(compareUpdatedDesc)
    .slice(0, 8);
  const lanes = activeLanes(proc, snapshots, events);

  return `
    <section class="rp-panel rp-active-panel">
      <div class="rp-panel-head">
        <div>
          <h3>正在执行</h3>
          <p>按实际进度聚合，不再区分单 worker 或多队列入口。</p>
        </div>
        ${proc.log ? `
          <button class="btn btn-secondary btn-sm rp-open-log" data-log="${escapeHtml(proc.log)}">
            <i data-lucide="file-text"></i>运行日志
          </button>
        ` : ""}
      </div>
      <div class="rp-active-grid">
        ${active.length
          ? active.map(renderProgressCard).join("")
          : `<div class="rp-empty">暂无进行中的任务</div>`}
      </div>
      ${lanes.length ? `
        <div class="rp-lane-summary">
          ${lanes.map(renderLaneCompact).join("")}
        </div>
      ` : ""}
    </section>
  `;
}

function renderProgressCard(item) {
  const percent = clampPercent(item.percent);
  return `
    <article class="rp-task-card">
      <div class="rp-task-head">
        <div>
          <strong>${escapeHtml(itemTitle(item))}</strong>
          <span>${escapeHtml(itemSubTitle(item))}</span>
        </div>
        ${statusIndicator(item.status)}
      </div>
      <div class="rp-progress-line">
        <div class="rp-progress-track">
          <i class="${item.status === "failed" ? "failed" : ""}" style="width:${percent}%"></i>
        </div>
        <span>${percent}%</span>
      </div>
      ${item.message ? `<p>${escapeHtml(item.message)}</p>` : ""}
      <time>${escapeHtml(formatDateTime(item.updated_at))}</time>
    </article>
  `;
}

function activeLanes(proc, snapshots, events) {
  const pools = Array.isArray(proc.lane_pools) ? proc.lane_pools : [];
  return pools.flatMap((pool) => {
    const lanes = Array.isArray(pool.lanes) ? pool.lanes : [];
    return lanes.map((lane) => ({
      ...lane,
      pool,
      progress: laneProgress(lane, snapshots, events),
    }));
  });
}

function laneProgress(lane, snapshots, events) {
  const attempts = Array.isArray(lane.build_attempt_ids) ? lane.build_attempt_ids : [];
  const relatedSnapshots = snapshots.filter((item) => attempts.some((id) => String(item.shard || "").includes(String(id))));
  if (relatedSnapshots.length) {
    return relatedSnapshots.slice().sort((a, b) => clampPercent(b.percent) - clampPercent(a.percent))[0];
  }
  const relatedEvents = events.filter((item) => attempts.some((id) => String(item.shard || "").includes(String(id))));
  if (relatedEvents.length) {
    const latest = relatedEvents[relatedEvents.length - 1];
    return { ...latest, updated_at: latest.created_at, percent: latest.percent || 0 };
  }
  return {
    stage: lane.running ? "queued" : "complete",
    status: lane.running ? "running" : (lane.returncode === 0 ? "completed" : "failed"),
    percent: lane.running ? 5 : 100,
    message: lane.message || "",
  };
}

function renderLaneCompact(lane) {
  const status = lane.running ? "running" : (lane.returncode === 0 ? "completed" : "failed");
  const percent = clampPercent(lane.progress?.percent);
  return `
    <article class="rp-lane-card">
      <div class="rp-lane-top">
        <div>
          <strong>队列 ${escapeHtml(lane.lane)}</strong>
          <span>${escapeHtml(lane.worker || "-")} · ${lane.queue_length || 0} 个任务</span>
        </div>
        ${statusIndicator(status)}
      </div>
      <div class="rp-progress-line">
        <div class="rp-progress-track">
          <i class="${status === "failed" ? "failed" : ""}" style="width:${percent}%"></i>
        </div>
        <span>${percent}%</span>
      </div>
      <p>${escapeHtml(lane.progress?.message || lane.message || "")}</p>
    </article>
  `;
}

function renderQueuePanel(proc, snapshots, events) {
  const queued = snapshots
    .filter((item) => item.status === "queued")
    .sort(compareUpdatedDesc)
    .slice(0, 10);
  const lanes = activeLanes(proc, snapshots, events);
  const waitingLanes = lanes.filter((lane) => lane.running && !lane.progress?.challenge_id);
  return `
    <section class="rp-panel">
      <div class="rp-panel-head">
        <div>
          <h3>等待队列</h3>
          <p>只展示待执行项和队列容量，避免把执行形态重复堆叠。</p>
        </div>
      </div>
      <div class="rp-queue-list">
        ${queued.length
          ? queued.map(renderQueueItem).join("")
          : waitingLanes.length
            ? waitingLanes.map(renderWaitingLane).join("")
            : `<div class="rp-empty compact">暂无等待中的任务</div>`}
      </div>
    </section>
  `;
}

function renderQueueItem(item) {
  return `
    <div class="rp-queue-item">
      <span class="dot ${dotTone(item.status)}"></span>
      <div>
        <strong>${escapeHtml(itemTitle(item))}</strong>
        <span>${escapeHtml(itemSubTitle(item))}</span>
      </div>
      <em>${escapeHtml(formatDateTime(item.updated_at))}</em>
    </div>
  `;
}

function renderWaitingLane(lane) {
  return `
    <div class="rp-queue-item">
      <span class="dot dot-warn"></span>
      <div>
        <strong>队列 ${escapeHtml(lane.lane)}</strong>
        <span>${escapeHtml(lane.worker || "-")} · ${lane.queue_length || 0} 个任务等待执行</span>
      </div>
      <em>${escapeHtml(runStatusLabel("running"))}</em>
    </div>
  `;
}

function renderRecentPanel(snapshots) {
  const rows = snapshots
    .filter((item) => FINISHED_STATUSES.has(item.status))
    .sort(compareUpdatedDesc)
    .slice(0, 8);
  return `
    <section class="rp-panel">
      <div class="rp-panel-head">
        <div>
          <h3>最近结果</h3>
          <p>最近完成或失败的进度快照。</p>
        </div>
      </div>
      <div class="rp-result-list">
        ${rows.length ? rows.map(renderResultItem).join("") : `<div class="rp-empty compact">暂无完成记录</div>`}
      </div>
    </section>
  `;
}

function renderResultItem(item) {
  return `
    <div class="rp-result-item">
      <div>
        <strong>${escapeHtml(itemTitle(item))}</strong>
        <span>${escapeHtml(itemSubTitle(item))}</span>
      </div>
      <div>
        ${statusIndicator(item.status)}
        <time>${escapeHtml(formatDateTime(item.updated_at))}</time>
      </div>
    </div>
  `;
}

function renderEventStream(events) {
  const recent = events.slice(-16).reverse();
  return `
    <aside class="rp-panel rp-events">
      <div class="rp-panel-head">
        <div>
          <h3>事件流</h3>
          <p>最近 16 条系统上报。</p>
        </div>
      </div>
      <div class="rp-event-list">
        ${recent.length ? recent.map(renderEvent).join("") : `<div class="rp-empty compact">暂无事件</div>`}
      </div>
    </aside>
  `;
}

function renderEvent(item) {
  return `
    <div class="rp-event">
      <span class="dot ${dotTone(item.status)}"></span>
      <div>
        <div>
          <strong>${escapeHtml(item.challenge_id || shortQueueName(item.shard))}</strong>
          <time>${escapeHtml(formatDateTime(item.created_at))}</time>
        </div>
        <span>${escapeHtml(stageLabel(item.stage))} · ${escapeHtml(runStatusLabel(item.status) || item.status)}</span>
        ${item.message ? `<p>${escapeHtml(item.message)}</p>` : ""}
      </div>
    </div>
  `;
}

function renderStorageWarning(progress) {
  const warning = progress?.storage?.warning || "";
  if (!warning) return "";
  return `<div class="rp-warning">${escapeHtml(warning)}</div>`;
}

function compareUpdatedDesc(a, b) {
  return String(b.updated_at || b.created_at || "").localeCompare(String(a.updated_at || a.created_at || ""));
}

export function render(data) {
  const root = document.querySelector('[data-view="worker-pool"]');
  if (!root) return;
  const proc = data.process || {};
  const progress = data.progress || {};
  const snapshots = snapshotsOf(progress);
  const events = eventsOf(progress);

  root.innerHTML = `
    ${renderStorageWarning(progress)}
    ${renderHero(proc, snapshots)}
    <div class="rp-layout">
      <main class="rp-main">
        ${renderActiveBoard(proc, snapshots, events)}
        ${renderQueuePanel(proc, snapshots, events)}
        ${renderRecentPanel(snapshots)}
      </main>
      ${renderEventStream(events)}
    </div>
  `;
}

export function bind() {
  document.addEventListener("click", (event) => {
    const root = document.querySelector('[data-view="worker-pool"]');
    if (!root || !root.contains(event.target)) return;

    const logBtn = event.target.closest(".rp-open-log");
    if (logBtn?.dataset.log) import("../router.js").then(({ setView }) => setView("logs"));
  });
}
