import { api } from "../api.js";
import { showToast } from "../ui/toast.js";
import { escapeHtml, formatDateTime } from "../ui/format.js";

// 统一日志页：合并 research-logs + 系统日志，按来源过滤
// 数据来源：
//   - 系统日志：data.logs（已有，文件名如 dashboard.log）
//   - 研究日志：/api/research/logs 接口（按需懒加载，文件名如 research-run-xxx.log）

let openName = null;
let openSource = "system"; // "research" | "build" | "system"
let researchLogs = [];
let researchLoaded = false;
let researchLoading = false;
let researchError = "";
let buildLogs = [];
let buildLoaded = false;
let buildLoading = false;
let buildError = "";
let filterSource = "all"; // "all" | "research" | "build" | "system"

function classifySource(name) {
  if (name.startsWith("research-")) return "research";
  if (name.startsWith("build-")) return "build";
  return "system";
}

function sourceLabel(source) {
  return { research: "Research", build: "Build", system: "系统" }[source] || "系统";
}

function sourcePillClass(source) {
  return {
    research: "pill pill-info",
    build: "pill pill-warning",
    system: "pill",
  }[source] || "pill";
}

async function ensureResearchLogs() {
  if (researchLoaded || researchLoading) return;
  researchLoading = true;
  researchError = "";
  try {
    researchLogs = await api("/api/research/logs");
    researchLoaded = true;
  } catch (err) {
    researchError = err.message;
    researchLogs = [];
  } finally {
    researchLoading = false;
  }
  renderLogsList();
}

async function ensureBuildLogs() {
  if (buildLoaded || buildLoading) return;
  buildLoading = true;
  buildError = "";
  try {
    buildLogs = await api("/api/build-attempts/logs");
    buildLoaded = true;
  } catch (err) {
    buildError = err.message;
    buildLogs = [];
  } finally {
    buildLoading = false;
  }
  renderLogsList();
}

function getAllLogItems(systemLogs) {
  const items = [];
  for (const log of researchLogs) {
    items.push({
      name: log.name,
      size: log.size,
      updated: log.updated_at,
      // 来自研究日志接口的记录一律归为 research，不再按文件名前缀误判为「系统」
      source: "research",
      endpoint: "research",
    });
  }
  for (const log of buildLogs) {
    items.push({
      name: log.name,
      size: log.size,
      updated: log.updated_at,
      source: "build",
      endpoint: "build",
    });
  }
  for (const log of systemLogs) {
    items.push({
      name: log.name,
      size: log.size,
      updated: log.updated,
      source: classifySource(log.name),
      endpoint: "system",
    });
  }
  return items;
}

function filterItems(items) {
  if (filterSource === "all") return items;
  if (filterSource === "research") return items.filter((item) => item.source === "research");
  if (filterSource === "build") return items.filter((item) => item.source === "build");
  if (filterSource === "system") return items.filter((item) => item.source === "system");
  return items;
}

function logListHtml(systemLogs) {
  if ((researchLoading && !researchLoaded) || (buildLoading && !buildLoaded)) {
    return `<div style="padding: var(--space-lg); text-align: center; color: var(--ink-500);">正在加载日志…</div>`;
  }
  if (researchError) {
    return `<div style="padding: var(--space-lg); color: var(--accent-red);">研究日志加载失败：${escapeHtml(researchError)}</div>`;
  }
  if (buildError) {
    return `<div style="padding: var(--space-lg); color: var(--accent-red);">构建日志加载失败：${escapeHtml(buildError)}</div>`;
  }
  const allItems = getAllLogItems(systemLogs);
  const filtered = filterItems(allItems);
  if (!filtered.length) {
    return `<div style="padding: var(--space-lg); text-align: center; color: var(--ink-500);">暂无日志</div>`;
  }
  return filtered.map((item) => `
    <button class="log-item${item.name === openName ? " active" : ""}" data-name="${escapeHtml(item.name)}" data-endpoint="${escapeHtml(item.endpoint)}">
      <div class="log-item-name">${escapeHtml(item.name)}</div>
      <div class="log-item-meta">
        <span class="${sourcePillClass(item.source)}">${sourceLabel(item.source)}</span>
        <span>${Math.ceil(item.size / 1024)} KB</span>
        <span>${escapeHtml(formatDateTime(item.updated))}</span>
      </div>
    </button>
  `).join("");
}

function renderLogsList() {
  const body = document.querySelector(".log-list-body");
  if (!body) return;
  const root = document.querySelector('[data-view="logs"]');
  if (!root) return;
  // 重新渲染时需要拿当前的系统日志数据，从全局 state 读取
  // 这里复用 render 重新触发
  render(window.__lastLogsData || { logs: [] });
}

export function render(data) {
  const root = document.querySelector('[data-view="logs"]');
  if (!root) return;
  window.__lastLogsData = data;
  const systemLogs = data.logs || [];

  // 懒加载研究日志 / 构建日志（首次进入页面时）
  if (!researchLoaded && !researchLoading) {
    ensureResearchLogs();
  }
  if (!buildLoaded && !buildLoading) {
    ensureBuildLogs();
  }

  root.innerHTML = `
    <div class="filter-bar filter-bar-standalone">
      <button class="filter-button${filterSource === "all" ? " active" : ""}" data-source="all">全部</button>
      <button class="filter-button${filterSource === "research" ? " active" : ""}" data-source="research">Research</button>
      <button class="filter-button${filterSource === "build" ? " active" : ""}" data-source="build">Build</button>
      <button class="filter-button${filterSource === "system" ? " active" : ""}" data-source="system">系统</button>
      <button id="refreshUnifiedLogs" class="btn btn-secondary btn-sm" style="margin-left: auto;" title="刷新日志列表">
        <i data-lucide="refresh-cw"></i>
      </button>
    </div>
    <div class="log-panel">
      <div class="log-list">
        <div class="log-list-header">日志文件</div>
        <div class="log-list-body">${logListHtml(systemLogs)}</div>
      </div>
      <div class="log-viewer">
        <div class="log-viewer-header">
          <span id="logTitle" class="log-viewer-title">${escapeHtml(openName || "选择日志")}</span>
          <button id="copyLog" class="log-viewer-copy" title="复制日志">
            <i data-lucide="copy"></i>
          </button>
        </div>
        <pre id="logContent" class="log-content">${openName ? "" : "暂无日志"}</pre>
      </div>
    </div>
  `;

  if (openName) loadLog(openName, openSource);
}

async function loadLog(name, endpoint) {
  try {
    const url = endpoint === "research"
      ? `/api/research/logs/${encodeURIComponent(name)}`
      : endpoint === "build"
        ? `/api/build-attempts/logs/${encodeURIComponent(name)}`
        : `/api/logs/${encodeURIComponent(name)}`;
    const result = await api(url);
    document.querySelector("#logTitle").textContent = result.name;
    document.querySelector("#logContent").textContent = result.content || "日志为空";
  } catch (error) {
    showToast(error.message, true);
  }
}

async function refreshAll() {
  researchLoaded = false;
  researchLogs = [];
  buildLoaded = false;
  buildLogs = [];
  await Promise.all([ensureResearchLogs(), ensureBuildLogs()]);
}

export function bind() {
  document.addEventListener("click", async (event) => {
    const root = document.querySelector('[data-view="logs"]');
    if (!root || !root.contains(event.target)) return;

    if (event.target.closest("#refreshUnifiedLogs")) {
      await refreshAll();
      return;
    }

    const filterBtn = event.target.closest("[data-source]");
    if (filterBtn && filterBtn.classList.contains("filter-button")) {
      filterSource = filterBtn.dataset.source;
      render(window.__lastLogsData || { logs: [] });
      return;
    }

    const button = event.target.closest(".log-item");
    if (button) {
      openName = button.dataset.name;
      openSource = button.dataset.endpoint || "system";
      document.querySelectorAll('[data-view="logs"] .log-item').forEach((node) => {
        node.classList.toggle("active", node === button);
      });
      await loadLog(openName, openSource);
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
