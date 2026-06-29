import { escapeHtml, stageLabel, statusIndicator, dotTone, formatDateTime } from "../ui/format.js";

function snapshotsHtml(snapshots) {
  if (!snapshots.length) {
    return `<div style="padding: var(--space-lg); text-align: center; color: var(--ink-500);">启动 Worker 后，这里会显示逐题阶段进度</div>`;
  }
  return snapshots.map((item) => {
    const barTone = item.status === "failed" ? "var(--accent-red)" : "var(--accent-green)";
    return `
      <article class="progress-item">
        <div class="progress-header">
          <div class="progress-info">
            <div style="display: flex; align-items: center; gap: var(--space-sm);">
              <span class="progress-id">${escapeHtml(item.challenge_id)}</span>
              ${statusIndicator(item.status)}
            </div>
            <div class="progress-meta">${escapeHtml(item.shard)} · ${escapeHtml(item.worker || "未分配")}</div>
          </div>
          <div style="text-align: right;">
            <div class="progress-percent">${item.percent}%</div>
            <div class="progress-stage">${escapeHtml(stageLabel(item.stage))}</div>
          </div>
        </div>
        <div class="progress-bar">
          <div class="progress-bar-fill${item.status === "failed" ? " err" : " ok"}" style="width:${item.percent}%"></div>
        </div>
        <div class="progress-footer">
          <span class="progress-message">${escapeHtml(item.message || "等待进度更新")}</span>
          <span class="progress-time">${escapeHtml(formatDateTime(item.updated_at))}</span>
        </div>
      </article>
    `;
  }).join("");
}

function eventsHtml(events) {
  if (!events.length) {
    return `<div style="padding: var(--space-lg); text-align: center; color: var(--ink-500);">暂无事件</div>`;
  }
  return events.map((item) => `
    <div class="timeline-item">
      <span class="timeline-dot dot ${dotTone(item.status)}" style="background: ${getDotColor(item.status)}"></span>
      <div class="timeline-header">
        <span class="timeline-title">${escapeHtml(item.challenge_id || item.shard)}</span>
        <span class="timeline-time">${escapeHtml(formatDateTime(item.created_at))}</span>
      </div>
      <div class="timeline-meta">${escapeHtml(stageLabel(item.stage))} · ${escapeHtml(item.status)}</div>
      ${item.message ? `<p class="timeline-message">${escapeHtml(item.message)}</p>` : ""}
    </div>
  `).join("");
}

function getDotColor(status) {
  if (status === "passed" || status === "done" || status === "completed") return "var(--accent-green)";
  if (status === "failed") return "var(--accent-red)";
  if (status === "running" || status === "queued") return "var(--accent-amber)";
  return "var(--ink-400)";
}

export function render(data) {
  const root = document.querySelector('[data-view="progress"]');
  if (!root) return;
  const snapshots = (data.progress.snapshots || []).filter((item) => item.challenge_id);
  const events = data.progress.events || [];
  const warning = data.progress.storage?.warning || "";

  root.innerHTML = `
    ${warning ? `
      <div style="margin-bottom: var(--space-md); border-radius: var(--radius-md); border: 1px solid var(--accent-amber-border); background: var(--accent-amber-light); padding: var(--space-md); color: #92400E;">
        ${escapeHtml(warning)}
      </div>
    ` : ""}
    <div style="display: grid; gap: var(--space-lg);">
      <section class="card">
        <div class="card-header">
          <div>
            <div class="card-title">逐题流水线</div>
            <div class="card-subtitle">设计、实现、Docker 构建、EXP 验证与文档阶段</div>
          </div>
        </div>
        <div class="progress-list">${snapshotsHtml(snapshots)}</div>
      </section>
      <section class="card card-body">
        <div style="margin-bottom: var(--space-lg);">
          <div class="card-title">事件时间线</div>
          <div class="card-subtitle">最近 60 条 Agent 与系统事件</div>
        </div>
        <div class="timeline">${eventsHtml(events)}</div>
      </section>
    </div>
  `;
}
