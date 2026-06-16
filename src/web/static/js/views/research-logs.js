import { api } from "../api.js";
import { showToast } from "../ui/toast.js";
import { escapeHtml } from "../ui/format.js";

let openName = null;
let logs = [];

async function loadLog(name) {
  try {
    const result = await api(`/api/logs/${encodeURIComponent(name)}`);
    document.querySelector("#researchLogTitle").textContent = result.name;
    document.querySelector("#researchLogContent").textContent = result.content || "日志为空";
  } catch (error) {
    showToast(error.message, true);
  }
}

function logListHtml(logs) {
  if (!logs.length) {
    return `<div style="padding: var(--space-lg); text-align: center; color: var(--ink-500);">暂无日志</div>`;
  }
  return logs.map((log) => `
    <button class="log-item${log.name === openName ? " active" : ""}" data-name="${escapeHtml(log.name)}">
      <div class="log-item-name">${escapeHtml(log.name)}</div>
      <div class="log-item-meta">
        <span>${Math.ceil(log.size / 1024)} KB</span>
        <span>${escapeHtml(log.updated)}</span>
      </div>
    </button>
  `).join("");
}

export function render(data) {
  const root = document.querySelector('[data-view="research-logs"]');
  if (!root) return;

  logs = data.logs || [];

  root.innerHTML = `
    <div class="log-panel">
      <div class="log-list">
        <div class="log-list-header">日志文件</div>
        <div class="log-list-body">${logListHtml(logs)}</div>
      </div>
      <div class="log-viewer">
        <div class="log-viewer-header">
          <span id="researchLogTitle" class="log-viewer-title">${escapeHtml(openName || "选择日志")}</span>
          <button id="copyResearchLog" class="log-viewer-copy" title="复制日志">
            <i data-lucide="copy"></i>
          </button>
        </div>
        <pre id="researchLogContent" class="log-content">${openName ? "" : "暂无日志"}</pre>
      </div>
    </div>
  `;

  if (openName) loadLog(openName);
}

export function bind() {
  document.addEventListener("click", async (event) => {
    const root = document.querySelector('[data-view="research-logs"]');
    if (!root || !root.contains(event.target)) return;

    const button = event.target.closest(".log-item");
    if (button) {
      openName = button.dataset.name;
      document.querySelectorAll('[data-view="research-logs"] .log-item').forEach((node) => {
        node.classList.toggle("active", node === button);
      });
      await loadLog(openName);
      return;
    }

    if (event.target.closest("#copyResearchLog")) {
      const text = document.querySelector("#researchLogContent")?.textContent || "";
      try {
        await navigator.clipboard.writeText(text);
        showToast("日志已复制");
      } catch {
        showToast("复制失败", true);
      }
    }
  });
}