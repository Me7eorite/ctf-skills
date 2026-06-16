import { api } from "../api.js";
import { showToast } from "../ui/toast.js";
import { escapeHtml, dotTone } from "../ui/format.js";

const STATES = [
  ["pending", "待处理"],
  ["running", "运行中"],
  ["failed", "失败"],
  ["done", "已完成"],
];

function shardCard(item, key, canRequeue) {
  return `
    <article class="card card-compact">
      <div style="display: flex; align-items: start; justify-content: space-between; gap: var(--space-sm);">
        <div style="min-width: 0;">
          <div class="truncate" style="font-size: var(--font-md); font-weight: 500; font-family: var(--font-mono-family);">${escapeHtml(item.name)}</div>
          <div style="font-size: var(--font-sm); color: var(--ink-500); margin-top: 2px;">${item.count} 题 · ${escapeHtml(item.categories.join(", ") || "-")}</div>
        </div>
        ${canRequeue ? `
          <button class="btn btn-icon-sm btn-secondary requeue-shard" data-state="${key}" data-name="${escapeHtml(item.name)}" title="重新入队">
            <i data-lucide="rotate-ccw"></i>
          </button>
        ` : ""}
      </div>
      <div style="display: flex; align-items: center; justify-content: space-between; margin-top: var(--space-md); font-size: var(--font-sm); color: var(--ink-500);">
        <span style="display: inline-flex; align-items: center;">
          <span class="dot ${dotTone(key)}"></span>${key}
        </span>
        <span>${escapeHtml(item.updated)}</span>
      </div>
    </article>
  `;
}

export function render(data) {
  const root = document.querySelector('[data-view="shards"]');
  if (!root) return;

  root.innerHTML = `
    <div style="display: grid; gap: var(--space-md); grid-template-columns: repeat(4, 1fr);">
      ${STATES.map(([key, label]) => {
        const rows = (data.shards || []).filter((item) => item.state === key);
        const canRequeue = (key === "failed") || (key === "running" && !data.process.running);
        const body = rows.length
          ? rows.map((item) => shardCard(item, key, canRequeue)).join("")
          : `<div class="empty empty-compact">空</div>`;

        return `
          <section>
            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: var(--space-sm);">
              <span style="font-size: var(--font-md); font-weight: 600;">${escapeHtml(label)}</span>
              <span class="pill">${rows.length}</span>
            </div>
            <div style="display: flex; flex-direction: column; gap: var(--space-sm);">${body}</div>
          </section>
        `;
      }).join("")}
    </div>
  `;
}

export function bind(reload) {
  document.addEventListener("click", async (event) => {
    const button = event.target.closest(".requeue-shard");
    if (!button) return;
    try {
      const result = await api(
        `/api/shards/${button.dataset.state}/${encodeURIComponent(button.dataset.name)}/requeue`,
        { method: "POST" }
      );
      showToast(result.message);
      await reload();
    } catch (error) {
      showToast(error.message, true);
    }
  });
}