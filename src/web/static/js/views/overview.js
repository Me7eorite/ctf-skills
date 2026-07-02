import {
  categoryLabel,
  categoryTone,
  dotTone,
  escapeHtml,
  softPill,
  statusIndicator,
} from "../ui/format.js";

const STAGES = [
  { key: "queue", label: "文件队列", icon: "list-ordered" },
  { key: "build", label: "构建", icon: "hammer" },
  { key: "validate", label: "验证", icon: "shield-check" },
  { key: "deliver", label: "交付", icon: "package-check" },
];

function numbers(summary) {
  const total = Number(summary?.challenges || 0);
  const built = Number(summary?.built || 0);
  const validated = Number(summary?.validated || 0);
  const delivery = Number(summary?.delivery_ready || validated || 0);
  const queue = summary?.queue || {};
  return {
    total,
    built,
    validated,
    delivery,
    activeQueue: Number(queue.pending || 0) + Number(queue.running || 0),
    pending: Number(queue.pending || 0),
    running: Number(queue.running || 0),
    failed: Number(queue.failed || 0),
    done: Number(queue.done || 0),
  };
}

function pipelineHero(summary, process) {
  const n = numbers(summary);
  const pct = n.total ? Math.round((n.delivery / n.total) * 100) : 0;
  const running = process?.running;
  return `
    <section class="ov-hero">
      <div class="ov-hero-main">
        <span class="ov-eyebrow">生产管线</span>
        <h2>${running ? "管线正在运行" : "管线空闲待命"}</h2>
        <p>从需求、题目设计、构建验证到最终交付的整体状态。</p>
        <div class="ov-hero-actions">
          <button class="btn btn-primary btn-sm" data-jump="research-submit">
            <i data-lucide="plus-circle"></i>新建需求
          </button>
          <button class="btn btn-secondary btn-sm" data-jump="build-attempts">
            <i data-lucide="hammer"></i>查看构建
          </button>
        </div>
      </div>
      <div class="ov-delivery-ring" style="--ov-progress:${pct}%">
        <div>
          <strong>${pct}%</strong>
          <span>交付完成度</span>
        </div>
      </div>
    </section>
  `;
}

function stageStrip(summary) {
  const n = numbers(summary);
  const values = {
    queue: n.activeQueue,
    build: n.built,
    validate: n.validated,
    deliver: n.delivery,
  };
  return `
    <section class="ov-stage-strip">
      ${STAGES.map((stage, index) => `
        <article class="ov-stage">
          <i data-lucide="${stage.icon}"></i>
          <div>
            <span>${escapeHtml(stage.label)}</span>
            <strong>${values[stage.key]}</strong>
          </div>
          ${index < STAGES.length - 1 ? `<em></em>` : ""}
        </article>
      `).join("")}
    </section>
  `;
}

function queuePanel(summary) {
  const n = numbers(summary);
  return `
    <section class="ov-panel ov-queue-panel">
      <div class="ov-panel-head">
        <div>
          <h3>队列态势</h3>
          <p>当前 shard 流转和异常积压。</p>
        </div>
        <button class="btn btn-secondary btn-sm" data-jump="shards">
          <i data-lucide="list-tree"></i>文件队列
        </button>
      </div>
      <div class="ov-queue-grid">
        ${queueItem("待运行", n.pending, "clock", "warning")}
        ${queueItem("运行中", n.running, "activity", "info")}
        ${queueItem("已完成", n.done, "check-check", "success")}
        ${queueItem("失败", n.failed, "triangle-alert", "danger")}
      </div>
    </section>
  `;
}

function queueItem(label, value, icon, tone) {
  return `
    <div class="ov-queue-item ov-${tone}">
      <i data-lucide="${icon}"></i>
      <span>${escapeHtml(label)}</span>
      <strong>${value}</strong>
    </div>
  `;
}

function categoryPanel(summary) {
  const categories = summary?.categories || {};
  const total = Math.max(1, Number(summary?.challenges || 0));
  return `
    <section class="ov-panel">
      <div class="ov-panel-head">
        <div>
          <h3>分类分布</h3>
          <p>当前完成题目的类型结构。</p>
        </div>
      </div>
      <div class="ov-category-bars">
        ${["web", "pwn", "re"].map((category) => {
          const count = Number(categories[category] || 0);
          const pct = Math.round((count / total) * 100);
          return `
            <div class="ov-category-row">
              <span>${softPill(categoryLabel(category), categoryTone(category))}</span>
              <div class="ov-category-track"><i style="width:${pct}%"></i></div>
              <strong>${count}</strong>
            </div>
          `;
        }).join("")}
      </div>
    </section>
  `;
}

function recentPanel(challenges) {
  const rows = [...(challenges || [])].slice(-5).reverse();
  return `
    <section class="ov-panel ov-recent-panel">
      <div class="ov-panel-head">
        <div>
          <h3>最近产出</h3>
          <p>最新写入的题目产物和验证结果。</p>
        </div>
        <button class="btn btn-secondary btn-sm" data-jump="challenges">
          <i data-lucide="flag"></i>完成题目
        </button>
      </div>
      <div class="ov-recent-list">
        ${rows.length ? rows.map(recentItem).join("") : `<div class="empty">暂无题目</div>`}
      </div>
    </section>
  `;
}

function recentItem(item) {
  return `
    <div class="ov-recent-item">
      <div class="ov-category-mark ${categoryClass(item.category)}">
        ${escapeHtml(categoryLabel(item.category).slice(0, 2))}
      </div>
      <div>
        <strong>${escapeHtml(item.title || item.id || "-")}</strong>
        <span>${escapeHtml(item.id || "-")} · ${escapeHtml(stackLabel(item))}</span>
      </div>
      <div class="ov-recent-status">
        ${statusIndicator(item.solve_status)}
      </div>
    </div>
  `;
}

function healthPanel(data) {
  const proc = data.process || {};
  const progress = data.progress?.snapshots || [];
  const runningSnapshots = progress.filter((item) => item.status === "running").length;
  const storage = data.progress?.storage || {};
  return `
    <section class="ov-panel">
      <div class="ov-panel-head">
        <div>
          <h3>运行健康</h3>
          <p>Worker、进度上报和存储状态。</p>
        </div>
        <button class="btn btn-secondary btn-sm" data-jump="worker-pool">
          <i data-lucide="activity"></i>实时进度
        </button>
      </div>
      <dl class="ov-health-list">
        ${healthRow("Worker", proc.running ? "运行中" : "空闲", proc.running ? "running" : "idle")}
        ${healthRow("进度任务", `${runningSnapshots} 个运行中`, runningSnapshots ? "running" : "idle")}
        ${healthRow("存储", storage.backend || "memory", storage.fallback ? "failed" : "passed")}
      </dl>
    </section>
  `;
}

function healthRow(label, value, status) {
  return `
    <div>
      <dt>${escapeHtml(label)}</dt>
      <dd><span class="dot ${dotTone(status)}"></span>${escapeHtml(value)}</dd>
    </div>
  `;
}

function stackLabel(item) {
  const runtime = item.runtime && item.runtime !== "-" ? item.runtime : "";
  const framework = item.framework && item.framework !== "-" ? item.framework : "";
  return [runtime, framework].filter(Boolean).join(" / ") || "-";
}

function categoryClass(category) {
  return ["web", "pwn", "re"].includes(category) ? category : "unknown";
}

export function render(data) {
  const root = document.querySelector('[data-view="overview"]');
  if (!root) return;
  root.innerHTML = `
    ${pipelineHero(data.summary || {}, data.process || {})}
    ${stageStrip(data.summary || {})}
    <div class="ov-grid">
      ${queuePanel(data.summary || {})}
      ${categoryPanel(data.summary || {})}
      ${healthPanel(data)}
      ${recentPanel(data.challenges || [])}
    </div>
  `;
}
