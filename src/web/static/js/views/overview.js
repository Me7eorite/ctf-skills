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
    <article class="metrics-card">
      <div class="metrics-header">
        <span>${escapeHtml(m.label)}</span>
        <span class="metrics-icon"><i data-lucide="${m.icon}"></i></span>
      </div>
      <div class="metrics-value">${m.value}</div>
      <div class="metrics-note">${escapeHtml(m.note)}</div>
    </article>
  `).join("");
}

function recentHtml(challenges) {
  const rows = challenges.slice(-6).reverse();
  if (!rows.length) {
    return `<div style="padding: var(--space-lg); text-align: center; color: var(--ink-500);">暂无题目</div>`;
  }
  return rows.map((item) => `
    <div style="display: flex; align-items: center; gap: var(--space-md); padding: 14px var(--space-lg); border-bottom: 1px solid var(--line);">
      <div style="width: 32px; height: 32px; display: grid; place-items: center; border-radius: var(--radius-md); font-size: var(--font-sm); font-weight: 600; ${categoryToneStyle(item.category)}">
        ${escapeHtml(categoryLabel(item.category).slice(0, 2))}
      </div>
      <div style="flex: 1; min-width: 0;">
        <div class="truncate" style="font-size: var(--font-md); font-weight: 500;">${escapeHtml(item.title)}</div>
        <div style="font-size: var(--font-sm); color: var(--ink-500); margin-top: 2px;">
          ${escapeHtml(item.id)} · ${escapeHtml(item.runtime)} / ${escapeHtml(item.framework)}
        </div>
      </div>
      ${statusIndicator(item.solve_status)}
    </div>
  `).join("");
}

function categoryToneStyle(code) {
  const meta = {
    web: "background: var(--cat-web-bg); color: var(--cat-web-text);",
    pwn: "background: var(--cat-pwn-bg); color: var(--cat-pwn-text);",
    re: "background: var(--cat-re-bg); color: var(--cat-re-text);",
  };
  return meta[code] || "background: var(--ink-100); color: var(--ink-700);";
}

export function render(data) {
  const root = document.querySelector('[data-view="overview"]');
  if (!root) return;
  root.innerHTML = `
    <div class="metrics-grid">${metricsHtml(data.summary)}</div>
    <div class="card" style="margin-top: var(--space-lg);">
      <div class="card-header">
        <div>
          <div class="card-title">最近题目</div>
          <div class="card-subtitle">构建与利用验证状态</div>
        </div>
        <button style="font-size: var(--font-md); font-weight: 500; color: var(--brand-600);" data-jump="challenges">
          查看全部 →
        </button>
      </div>
      <div>${recentHtml(data.challenges)}</div>
    </div>
  `;
}