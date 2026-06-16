import { api } from "../api.js";
import { showToast } from "../ui/toast.js";
import { escapeHtml } from "../ui/format.js";

let openName = null;
let logs = [];
let loaded = false;
let loading = false;
let error = "";

async function loadLogs() {
  if (loading) return;
  loading = true;
  error = "";
  try {
    logs = await api("/api/research/logs");
    loaded = true;
  } catch (err) {
    logs = [];
    error = err.message;
  } finally {
    loading = false;
  }
}

async function refreshLogs() {
  await loadLogs();
  render();
  window.lucide?.createIcons();
}

async function loadLog(name) {
  try {
    const result = await api(`/api/research/logs/${encodeURIComponent(name)}`);
    document.querySelector("#researchLogTitle").textContent = result.name;
    document.querySelector("#researchLogContent").textContent = result.content || "Log is empty";
  } catch (err) {
    showToast(err.message, true);
  }
}

function logListHtml() {
  if (loading && !loaded) {
    return `<div style="padding: var(--space-lg); text-align: center; color: var(--ink-500);">Loading logs...</div>`;
  }
  if (error) {
    return `<div style="padding: var(--space-lg); color: var(--accent-red);">${escapeHtml(error)}</div>`;
  }
  if (!logs.length) {
    return `<div style="padding: var(--space-lg); text-align: center; color: var(--ink-500);">No research logs</div>`;
  }
  return logs.map((log) => `
    <button class="log-item${log.name === openName ? " active" : ""}" data-name="${escapeHtml(log.name)}">
      <div class="log-item-name">${escapeHtml(log.name)}</div>
      <div class="log-item-meta">
        <span>${Math.ceil(log.size / 1024)} KB</span>
        <span>${escapeHtml((log.updated_at || "").slice(0, 19))}</span>
      </div>
    </button>
  `).join("");
}

export function render() {
  const root = document.querySelector('[data-view="research-logs"]');
  if (!root) return;
  if (!loaded && !loading) refreshLogs();

  root.innerHTML = `
    <div class="log-panel">
      <div class="log-list">
        <div class="log-list-header" style="display: flex; align-items: center; justify-content: space-between;">
          <span>Research logs</span>
          <button id="refreshResearchLogs" class="btn btn-secondary btn-sm" title="Refresh logs">
            <i data-lucide="refresh-cw"></i>
          </button>
        </div>
        <div class="log-list-body">${logListHtml()}</div>
      </div>
      <div class="log-viewer">
        <div class="log-viewer-header">
          <span id="researchLogTitle" class="log-viewer-title">${escapeHtml(openName || "Select a log")}</span>
          <button id="copyResearchLog" class="log-viewer-copy" title="Copy log">
            <i data-lucide="copy"></i>
          </button>
        </div>
        <pre id="researchLogContent" class="log-content">${openName ? "" : "No log selected"}</pre>
      </div>
    </div>
  `;

  if (openName) loadLog(openName);
}

export function bind() {
  document.addEventListener("click", async (event) => {
    const root = document.querySelector('[data-view="research-logs"]');
    if (!root || !root.contains(event.target)) return;

    if (event.target.closest("#refreshResearchLogs")) {
      loaded = false;
      await refreshLogs();
      return;
    }

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
        showToast("Log copied");
      } catch {
        showToast("Copy failed", true);
      }
    }
  });
}
