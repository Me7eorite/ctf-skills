import { api } from "../api.js";
import { showToast } from "../ui/toast.js";
import { escapeHtml } from "../ui/format.js";

let openName = null;

function logListHtml(logs) {
  if (!logs.length) {
    return `<div class="px-4 py-12 text-center text-[13px] text-ink-500">暂无日志</div>`;
  }
  return logs.map((log) => `
    <button class="log-button block w-full px-4 py-3 text-left transition-colors hover:bg-ink-50 ${log.name === openName ? "bg-ink-50" : ""}"
      data-name="${escapeHtml(log.name)}">
      <div class="truncate text-[13px] font-medium font-mono">${escapeHtml(log.name)}</div>
      <div class="mt-1 flex justify-between text-[11px] text-ink-500 tabular-nums">
        <span>${Math.ceil(log.size / 1024)} KB</span><span>${escapeHtml(log.updated)}</span>
      </div>
    </button>
  `).join("");
}

export function render(data) {
  const root = document.querySelector('[data-view="logs"]');
  if (!root) return;
  root.innerHTML = `
    <div class="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
      <div class="card overflow-hidden">
        <div class="border-b border-line px-4 py-3 text-[13px] font-semibold">日志文件</div>
        <div class="divide-y divide-line max-h-[600px] overflow-auto">${logListHtml(data.logs || [])}</div>
      </div>
      <div class="card min-w-0 overflow-hidden bg-[#1A1D21] !border-[#2A2E33]">
        <div class="flex h-11 items-center justify-between border-b border-white/5 px-4 text-[11px] text-neutral-400">
          <span id="logTitle" class="font-mono">${escapeHtml(openName || "选择日志")}</span>
          <button id="copyLog" class="grid size-8 place-items-center rounded-md hover:bg-white/10 hover:text-white" title="复制日志">
            <i data-lucide="copy" class="size-4"></i>
          </button>
        </div>
        <pre id="logContent" class="log-pre h-[520px] overflow-auto whitespace-pre-wrap p-4 font-mono text-[12px] leading-5 text-neutral-200">${
          openName ? "" : "暂无日志"
        }</pre>
      </div>
    </div>
  `;
  if (openName) loadLog(openName);
}

async function loadLog(name) {
  try {
    const result = await api(`/api/logs/${encodeURIComponent(name)}`);
    document.querySelector("#logTitle").textContent = result.name;
    document.querySelector("#logContent").textContent = result.content || "日志为空";
  } catch (error) {
    showToast(error.message, true);
  }
}

export function bind() {
  document.addEventListener("click", async (event) => {
    const button = event.target.closest(".log-button");
    if (button) {
      openName = button.dataset.name;
      document.querySelectorAll(".log-button").forEach((node) => {
        node.classList.toggle("bg-ink-50", node === button);
      });
      await loadLog(openName);
      return;
    }
    if (event.target.closest("#copyLog")) {
      const text = document.querySelector("#logContent")?.textContent || "";
      try {
        await navigator.clipboard.writeText(text);
        showToast("日志已复制");
      } catch {
        showToast("复制失败", true);
      }
    }
  });
}
