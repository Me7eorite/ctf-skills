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
    <article class="card p-4">
      <div class="flex items-start justify-between gap-2">
        <div class="min-w-0">
          <div class="truncate text-[13px] font-medium font-mono">${escapeHtml(item.name)}</div>
          <div class="mt-1 text-[11px] text-ink-500">${item.count} 题 · ${escapeHtml(item.categories.join(", ") || "-")}</div>
        </div>
        ${canRequeue ? `<button class="requeue-shard grid size-8 shrink-0 place-items-center rounded-md border border-line hover:bg-ink-50"
          data-state="${key}" data-name="${escapeHtml(item.name)}" title="重新入队">
          <i data-lucide="rotate-ccw" class="size-4"></i>
        </button>` : ""}
      </div>
      <div class="mt-4 flex items-center justify-between text-[11px] text-ink-500">
        <span class="inline-flex items-center"><span class="dot ${dotTone(key)}"></span>${key}</span>
        <span class="tabular-nums">${escapeHtml(item.updated)}</span>
      </div>
    </article>
  `;
}

export function render(data) {
  const root = document.querySelector('[data-view="shards"]');
  if (!root) return;
  root.innerHTML = `<div class="grid gap-4 md:grid-cols-2 xl:grid-cols-4">${
    STATES.map(([key, label]) => {
      const rows = (data.shards || []).filter((item) => item.state === key);
      const canRequeue = (key === "failed") || (key === "running" && !data.process.running);
      const body = rows.length
        ? rows.map((item) => shardCard(item, key, canRequeue)).join("")
        : `<div class="empty">空</div>`;
      return `
        <section>
          <div class="mb-3 flex h-8 items-center justify-between">
            <span class="text-[13px] font-semibold">${escapeHtml(label)}</span>
            <span class="grid h-5 min-w-[20px] place-items-center rounded-full bg-ink-100 px-1.5 text-[11px] text-ink-700">${rows.length}</span>
          </div>
          <div class="space-y-2">${body}</div>
        </section>
      `;
    }).join("")
  }</div>`;
}

export function bind(reload) {
  document.addEventListener("click", async (event) => {
    const button = event.target.closest(".requeue-shard");
    if (!button) return;
    try {
      const result = await api(
        `/api/shards/${button.dataset.state}/${encodeURIComponent(button.dataset.name)}/requeue`,
        { method: "POST" },
      );
      showToast(result.message);
      await reload();
    } catch (error) {
      showToast(error.message, true);
    }
  });
}
