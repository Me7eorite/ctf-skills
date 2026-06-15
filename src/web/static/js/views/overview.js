import { escapeHtml, categoryLabel, categoryTone, statusIndicator } from "../ui/format.js";

function metricsHtml(summary) {
  const active = summary.queue.pending + summary.queue.running;
  const validatedPct = summary.challenges ? Math.round(summary.validated / summary.challenges * 100) : 0;
  const metrics = [
    {
      label: "题目总数", value: summary.challenges, icon: "flag",
      note: `Web ${summary.categories.web} · Pwn ${summary.categories.pwn} · Re ${summary.categories.re}`,
    },
    {
      label: "EXP 已通过", value: summary.validated, icon: "shield-check",
      note: `${validatedPct}% 验证率`,
    },
    {
      label: "构建已通过", value: summary.built, icon: "package-check",
      note: `${Math.max(0, summary.challenges - summary.built)} 个待处理`,
    },
    {
      label: "活动队列", value: active, icon: "layers-3",
      note: `${summary.queue.failed} 失败 · ${summary.queue.done} 完成`,
    },
  ];
  return metrics.map((m) => `
    <article class="card p-5">
      <div class="flex items-center justify-between text-[11px] font-medium text-ink-500">
        <span>${escapeHtml(m.label)}</span>
        <i data-lucide="${m.icon}" class="size-4 text-ink-400"></i>
      </div>
      <div class="mt-4 text-3xl font-semibold tracking-tight">${m.value}</div>
      <div class="mt-2 truncate text-[11px] text-ink-500">${escapeHtml(m.note)}</div>
    </article>
  `).join("");
}

function recentHtml(challenges) {
  const rows = challenges.slice(-6).reverse();
  if (!rows.length) {
    return `<div class="px-5 py-12 text-center text-[13px] text-ink-500">暂无题目</div>`;
  }
  return rows.map((item) => `
    <div class="flex items-center gap-3 px-5 py-3.5">
      <div class="grid size-8 shrink-0 place-items-center rounded-md ${categoryTone(item.category)} text-[11px] font-semibold">
        ${escapeHtml(categoryLabel(item.category).slice(0, 2))}
      </div>
      <div class="min-w-0 flex-1">
        <div class="truncate text-[13px] font-medium">${escapeHtml(item.title)}</div>
        <div class="mt-0.5 truncate text-[11px] text-ink-500">
          ${escapeHtml(item.id)} · ${escapeHtml(item.runtime)} / ${escapeHtml(item.framework)}
        </div>
      </div>
      ${statusIndicator(item.solve_status)}
    </div>
  `).join("");
}

export function render(data) {
  const root = document.querySelector('[data-view="overview"]');
  if (!root) return;
  root.innerHTML = `
    <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">${metricsHtml(data.summary)}</div>
    <div class="mt-6 card">
      <div class="card-header">
        <div>
          <div class="card-title">最近题目</div>
          <div class="card-subtitle">构建与利用验证状态</div>
        </div>
        <button class="text-[13px] font-medium text-brand-600 hover:text-brand-700" data-jump="challenges">
          查看全部 →
        </button>
      </div>
      <div class="divide-y divide-line">${recentHtml(data.challenges)}</div>
    </div>
  `;
}
