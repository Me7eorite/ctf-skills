import { escapeHtml, stageLabel, statusIndicator, dotTone, runStatusLabel } from "../ui/format.js";

function getDotColor(status) {
  if (status === "passed" || status === "done" || status === "completed") return "var(--accent-green)";
  if (status === "failed") return "var(--accent-red)";
  if (status === "running" || status === "queued") return "var(--accent-amber)";
  return "var(--ink-400)";
}

function renderWorkerCard(proc, snapshots) {
  const running = !!proc.running;
  const dotClass = running ? "running" : "idle";
  const stateLabel = running ? (proc.message || "运行中") : "空闲";
  const current = snapshots.find((item) => item.status === "running" || item.status === "queued");
  const nextSnapshots = snapshots.filter((item) => item !== current && item.status === "queued").slice(0, 1);

  return `
    <article class="card wp-worker-card">
      <div class="card-header">
        <div class="wp-worker-header-left">
          <span class="sidebar-worker-dot ${dotClass}"></span>
          <div>
            <div class="card-title">worker-01</div>
            <div class="card-subtitle">${escapeHtml(stateLabel)}</div>
          </div>
        </div>
        <span class="pill wp-worker-state-${dotClass}">${escapeHtml(running ? "running" : "idle")}</span>
      </div>
      <div class="card-body">
        ${current ? `
          <div class="wp-current-task">
            <div class="wp-task-row">
              <span class="wp-task-id">${escapeHtml(current.challenge_id || "—")}</span>
              ${statusIndicator(current.status)}
            </div>
            <div class="wp-task-progress">
              <div class="sidebar-worker-bar">
                <div class="sidebar-worker-bar-fill ${current.status === "failed" ? "idle" : "running"}" style="width:${current.percent || 0}%"></div>
              </div>
              <span class="wp-task-percent">${escapeHtml(String(current.percent || 0))}%</span>
            </div>
            <div class="wp-task-meta">${escapeHtml(stageLabel(current.stage))}${current.message ? ` · ${escapeHtml(current.message)}` : ""}</div>
          </div>
        ` : `
          <div class="wp-idle">暂无进行中的任务</div>
        `}
        ${nextSnapshots.length ? `
          <div class="wp-next-task">
            <span class="wp-next-label">下一题</span>
            <span class="wp-next-id">${escapeHtml(nextSnapshots[0].challenge_id || "—")}</span>
            <span class="wp-next-state">queued</span>
          </div>
        ` : ""}
        ${proc.log_path ? `
          <div class="wp-worker-actions">
            <button class="btn btn-ghost btn-sm wp-open-log" data-log="${escapeHtml(proc.log_path)}">
              <i data-lucide="file-text"></i> 查看日志
            </button>
          </div>
        ` : ""}
      </div>
    </article>
  `;
}

function renderRecentCompletions(snapshots) {
  const completed = snapshots
    .filter((item) => ["passed", "done", "completed", "failed"].includes(item.status))
    .slice(-5)
    .reverse();
  if (!completed.length) {
    return "";
  }
  return `
    <section class="card wp-recent-card">
      <div class="card-header">
        <div>
          <div class="card-title">最近完成</div>
          <div class="card-subtitle">最近 5 条已结束的执行</div>
        </div>
      </div>
      <div class="table-container">
        <table class="table">
          <thead>
            <tr>
              <th>题目</th>
              <th>状态</th>
              <th>阶段</th>
              <th>更新时间</th>
            </tr>
          </thead>
          <tbody>
            ${completed.map((item) => `
              <tr>
                <td class="table-cell-mono">${escapeHtml(item.challenge_id || "—")}</td>
                <td>${statusIndicator(item.status)}</td>
                <td>${escapeHtml(stageLabel(item.stage))}</td>
                <td class="table-cell-time">${escapeHtml(item.updated_at || "—")}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function renderEventPanel(events) {
  if (!events || !events.length) {
    return "";
  }
  const recent = events.slice(-20).reverse();
  return `
    <details class="card wp-events-card">
      <summary class="wp-events-summary">
        <span class="card-title">最近事件</span>
        <span class="card-subtitle">最近 20 条 Agent 与系统事件（默认收起）</span>
      </summary>
      <div class="wp-events-list">
        ${recent.map((item) => `
          <div class="wp-event-item">
            <span class="dot ${dotTone(item.status)}" style="background: ${getDotColor(item.status)}"></span>
            <div class="wp-event-body">
              <div class="wp-event-head">
                <span class="wp-event-title">${escapeHtml(item.challenge_id || item.shard || "—")}</span>
                <span class="wp-event-time">${escapeHtml(item.created_at || "")}</span>
              </div>
              <div class="wp-event-meta">${escapeHtml(stageLabel(item.stage))} · ${escapeHtml(runStatusLabel(item.status) || item.status)}</div>
              ${item.message ? `<p class="wp-event-message">${escapeHtml(item.message)}</p>` : ""}
            </div>
          </div>
        `).join("")}
      </div>
    </details>
  `;
}

function renderSummaryBar(proc, snapshots) {
  const running = snapshots.filter((item) => item.status === "running").length;
  const queued = snapshots.filter((item) => item.status === "queued").length;
  const failed = snapshots.filter((item) => item.status === "failed").length;
  return `
    <div class="wp-summary-bar">
      <div class="wp-summary-item">
        <span class="sidebar-worker-dot ${proc.running ? "running" : "idle"}"></span>
        <span class="wp-summary-label">${proc.running ? "运行中" : "空闲"}</span>
      </div>
      <div class="wp-summary-item">
        <span class="wp-summary-num">${running}</span>
        <span class="wp-summary-label">运行</span>
      </div>
      <div class="wp-summary-item">
        <span class="wp-summary-num">${queued}</span>
        <span class="wp-summary-label">队列</span>
      </div>
      <div class="wp-summary-item">
        <span class="wp-summary-num">${failed}</span>
        <span class="wp-summary-label">失败</span>
      </div>
    </div>
  `;
}

export function render(data) {
  const root = document.querySelector('[data-view="worker-pool"]');
  if (!root) return;
  const proc = data.process || {};
  const progress = data.progress || { snapshots: [], events: [] };
  const snapshots = (progress.snapshots || []).filter((item) => item.challenge_id);
  const events = progress.events || [];
  const warning = progress.storage?.warning || "";

  root.innerHTML = `
    ${warning ? `
      <div class="wp-warning">${escapeHtml(warning)}</div>
    ` : ""}
    ${renderSummaryBar(proc, snapshots)}
    <div class="wp-worker-grid">
      ${renderWorkerCard(proc, snapshots)}
    </div>
    ${renderRecentCompletions(snapshots)}
    ${renderEventPanel(events)}
  `;
}

export function bind() {
  document.addEventListener("click", (event) => {
    const root = document.querySelector('[data-view="worker-pool"]');
    if (!root || !root.contains(event.target)) return;

    const logBtn = event.target.closest(".wp-open-log");
    if (logBtn?.dataset.log) {
      // 跳转到统一日志页
      import("../router.js").then(({ setView }) => setView("logs"));
    }
  });
}
