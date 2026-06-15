import { escapeHtml, stageLabel, statusIndicator, dotTone } from "../ui/format.js";

function snapshotsHtml(snapshots) {
  if (!snapshots.length) {
    return `<div class="px-5 py-16 text-center text-[13px] text-ink-500">启动 Worker 后，这里会显示逐题阶段进度</div>`;
  }
  return snapshots.map((item) => {
    const barTone = item.status === "failed" ? "bg-rose-500" : "bg-emerald-500";
    return `
      <article class="border-b border-line px-5 py-4 last:border-b-0">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex items-center gap-2">
              <span class="font-mono text-[13px] font-semibold">${escapeHtml(item.challenge_id)}</span>
              ${statusIndicator(item.status)}
            </div>
            <div class="mt-1 truncate text-[11px] text-ink-500">${escapeHtml(item.shard)} · ${escapeHtml(item.worker || "未分配")}</div>
          </div>
          <div class="text-right">
            <div class="text-[13px] font-semibold tabular-nums">${item.percent}%</div>
            <div class="mt-1 text-[11px] text-ink-500">${escapeHtml(stageLabel(item.stage))}</div>
          </div>
        </div>
        <div class="mt-3 h-1.5 overflow-hidden rounded-full bg-ink-100">
          <div class="h-full transition-all ${barTone}" style="width:${item.percent}%"></div>
        </div>
        <div class="mt-2 flex items-center justify-between gap-4 text-[11px] text-ink-500">
          <span class="truncate">${escapeHtml(item.message || "等待进度更新")}</span>
          <span class="shrink-0 tabular-nums">${escapeHtml(item.updated_at)}</span>
        </div>
      </article>
    `;
  }).join("");
}

function eventsHtml(events) {
  if (!events.length) {
    return `<div class="py-10 text-center text-[13px] text-ink-500">暂无事件</div>`;
  }
  return events.map((item) => `
    <div class="relative border-l border-line pb-5 pl-5 last:pb-0">
      <span class="absolute -left-[5px] top-1.5 size-2.5 rounded-full border-2 border-surface dot ${dotTone(item.status)}"></span>
      <div class="flex items-center justify-between gap-3">
        <span class="truncate text-[13px] font-medium">${escapeHtml(item.challenge_id || item.shard)}</span>
        <span class="shrink-0 text-[11px] text-ink-400 tabular-nums">${escapeHtml(item.created_at)}</span>
      </div>
      <div class="mt-1 text-[11px] text-ink-500">${escapeHtml(stageLabel(item.stage))} · ${escapeHtml(item.status)}</div>
      ${item.message ? `<p class="mt-1 text-[11px] leading-5 text-ink-600">${escapeHtml(item.message)}</p>` : ""}
    </div>
  `).join("");
}

export function render(data) {
  const root = document.querySelector('[data-view="progress"]');
  if (!root) return;
  const snapshots = (data.progress.snapshots || []).filter((item) => item.challenge_id);
  const events = data.progress.events || [];
  const warning = data.progress.storage?.warning || "";
  root.innerHTML = `
    ${warning ? `<div class="mb-4 rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-[13px] text-amber-900">${escapeHtml(warning)}</div>` : ""}
    <div class="grid gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(300px,.65fr)]">
      <section class="card">
        <div class="card-header"><div>
          <div class="card-title">逐题流水线</div>
          <div class="card-subtitle">设计、实现、Docker 构建、EXP 验证与文档阶段</div>
        </div></div>
        <div>${snapshotsHtml(snapshots)}</div>
      </section>
      <section class="card p-5">
        <div class="mb-5">
          <div class="card-title">事件时间线</div>
          <div class="card-subtitle">最近 60 条 Agent 与系统事件</div>
        </div>
        <div class="max-h-[680px] overflow-auto pl-2">${eventsHtml(events)}</div>
      </section>
    </div>
  `;
}
