import { appState } from "../state.js";
import { initIcons } from "../ui/icons.js";
import { showToast } from "../ui/toast.js";
import {
  categoryLabel,
  categoryTone,
  dotTone,
  escapeHtml,
  softPill,
} from "../ui/format.js";

const CATEGORIES = ["web", "pwn", "re"];

function challengeStats(rows) {
  return rows.reduce((acc, item) => {
    acc.total += 1;
    if (item.build_status === "passed") acc.built += 1;
    if (item.solve_status === "passed") acc.solved += 1;
    if (isDeliveryReady(item)) acc.delivery += 1;
    if (item.category in acc.categories) acc.categories[item.category] += 1;
    return acc;
  }, {
    total: 0,
    built: 0,
    solved: 0,
    delivery: 0,
    categories: { web: 0, pwn: 0, re: 0 },
  });
}

function isDeliveryReady(item) {
  return item.delivery_ready === true
    || (item.build_status === "passed" && item.solve_status === "passed");
}

function metricCard(label, value, icon, tone) {
  return `
    <article class="ch-metric ch-metric-${tone}">
      <i data-lucide="${icon}"></i>
      <div>
        <strong>${value}</strong>
        <span>${escapeHtml(label)}</span>
      </div>
    </article>
  `;
}

function rowsHtml(rows) {
  if (!rows.length) {
    return `<tr><td colspan="9" class="table-empty">没有匹配的题目</td></tr>`;
  }
  return rows.map((item) => `
    <tr>
      <td>
        <div class="ch-title">${escapeHtml(item.title || item.id)}</div>
        <div class="ch-id mono">${escapeHtml(item.id)}</div>
      </td>
      <td>${softPill(categoryLabel(item.category), categoryTone(item.category))}</td>
      <td>${escapeHtml(difficultyLabel(item.difficulty))}</td>
      <td>${escapeHtml(stackLabel(item))}</td>
      <td>${statusBadge(item.build_status, "build")}</td>
      <td>${statusBadge(item.solve_status, "solve")}</td>
      <td>${deliveryBadge(item)}</td>
      <td>${rowActions(item)}</td>
      <td class="table-cell-time">${escapeHtml(item.updated)}</td>
    </tr>
  `).join("");
}

function cardsHtml(rows) {
  if (!rows.length) return `<div class="empty card-body">没有匹配的题目</div>`;
  return rows.map((item) => `
    <article class="ch-card">
      <div class="ch-card-head">
        <div>
          <strong>${escapeHtml(item.title || item.id)}</strong>
          <span class="mono">${escapeHtml(item.id)}</span>
        </div>
        ${deliveryBadge(item)}
      </div>
      <div class="ch-card-badges">
        ${softPill(categoryLabel(item.category), categoryTone(item.category))}
        ${statusBadge(item.build_status, "build")}
        ${statusBadge(item.solve_status, "solve")}
      </div>
      <dl class="ch-card-meta">
        <div><dt>难度</dt><dd>${escapeHtml(difficultyLabel(item.difficulty))}</dd></div>
        <div><dt>技术栈</dt><dd>${escapeHtml(stackLabel(item))}</dd></div>
        <div><dt>更新</dt><dd>${escapeHtml(item.updated)}</dd></div>
      </dl>
      <div class="ch-card-actions">${rowActions(item)}</div>
    </article>
  `).join("");
}

function filterButton(code, label, count = null) {
  const active = appState.category === code;
  return `
    <button class="filter-button${active ? " active" : ""}" data-category="${code}">
      ${escapeHtml(label)}${count === null ? "" : `<span>${count}</span>`}
    </button>
  `;
}

function statusBadge(status, kind) {
  const label = statusLabel(status, kind);
  return `<span class="ch-status"><span class="dot ${dotTone(status)}"></span>${escapeHtml(label)}</span>`;
}

function statusLabel(status, kind) {
  if (status === "passed") return "通过";
  if (status === "failed") return "失败";
  if (status === "running") return "运行中";
  if (status === "pending") return "待验证";
  if (kind === "build") return "未构建";
  if (kind === "solve") return "未验证";
  return status || "未知";
}

function deliveryBadge(item) {
  return isDeliveryReady(item)
    ? softPill("可交付", "text-emerald-700 bg-emerald-50")
    : softPill("未就绪", "text-ink-700 bg-ink-100");
}

function rowActions(item) {
  const ready = isDeliveryReady(item);
  const busy = appState.challengeDeliveryDownloadingId === item.id;
  if (!ready) {
    return softPill("未就绪", "text-ink-700 bg-ink-100");
  }
  const disabled = busy;
  return `
    <button class="btn btn-secondary btn-sm ch-download-btn" data-challenge-id="${escapeHtml(item.id)}"${disabled ? " disabled" : ""}>
      <i data-lucide="download"></i>${busy ? "打包中" : "下载"}
    </button>
  `;
}

function difficultyLabel(value) {
  return {
    easy: "简单",
    medium: "中等",
    hard: "困难",
    expert: "专家",
  }[value] || value || "-";
}

function stackLabel(item) {
  const runtime = item.runtime && item.runtime !== "-" ? item.runtime : "";
  const framework = item.framework && item.framework !== "-" ? item.framework : "";
  return [runtime, framework].filter(Boolean).join(" · ") || "-";
}

function filteredRows(data) {
  const query = appState.search.toLowerCase();
  return (data.challenges || []).filter((item) => {
    const cat = appState.category === "all" || item.category === appState.category;
    const haystack = `${item.id} ${item.title} ${item.runtime} ${item.framework}`.toLowerCase();
    return cat && (!query || haystack.includes(query));
  });
}

async function downloadDelivery() {
  if (appState.challengeDeliveryDownloading) return;
  appState.challengeDeliveryDownloading = true;
  render(appState.data);
  initIcons();
  try {
    const response = await fetch("/api/challenges/delivery/download");
    if (!response.ok) {
      let payload = {};
      try { payload = await response.json(); } catch { /* ignore parse */ }
      throw new Error(payload.detail || payload.message || `下载失败 (${response.status})`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "完成题目交付包.zip";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showToast("交付包已生成");
  } catch (err) {
    showToast(err.message, true);
  } finally {
    appState.challengeDeliveryDownloading = false;
    render(appState.data);
    initIcons();
  }
}

async function downloadSingleDelivery(challengeId) {
  if (!challengeId || appState.challengeDeliveryDownloadingId) return;
  appState.challengeDeliveryDownloadingId = challengeId;
  render(appState.data);
  initIcons();
  try {
    const response = await fetch(`/api/challenges/${encodeURIComponent(challengeId)}/delivery/download`);
    if (!response.ok) {
      let payload = {};
      try { payload = await response.json(); } catch { /* ignore parse */ }
      throw new Error(payload.detail || payload.message || `下载失败 (${response.status})`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${challengeId}-交付包.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showToast("单题交付包已生成");
  } catch (err) {
    showToast(err.message, true);
  } finally {
    appState.challengeDeliveryDownloadingId = null;
    render(appState.data);
    initIcons();
  }
}

export function render(data) {
  const root = document.querySelector('[data-view="challenges"]');
  if (!root) return;
  const rows = data.challenges || [];
  const filtered = filteredRows(data);
  const stats = challengeStats(rows);
  const filteredStats = challengeStats(filtered);
  const downloading = appState.challengeDeliveryDownloading;

  root.innerHTML = `
    <div class="ch-page-header">
      <div>
        <h2 class="ch-page-title">完成题目</h2>
        <p class="ch-page-desc">累计 ${stats.total} 题 · 当前筛选 ${filtered.length} 题</p>
      </div>
      <button id="challengeDeliveryDownload" class="btn btn-primary btn-sm${downloading ? " btn-loading" : ""}"
        ${stats.delivery ? "" : "disabled"}>
        <i data-lucide="download"></i>${downloading ? "打包中" : `下载交付包 · ${stats.delivery}`}
      </button>
    </div>

    <div class="ch-summary-grid">
      ${metricCard("累计完成", stats.total, "flag", "neutral")}
      ${metricCard("可交付", stats.delivery, "package-check", "success")}
      ${metricCard("构建通过", stats.built, "hammer", "info")}
      ${metricCard("EXP 通过", stats.solved, "shield-check", "warning")}
    </div>

    <section class="card ch-list-card">
      <div class="ch-list-summary">
        <div>
          <div class="card-title">题目清单</div>
          <div class="card-subtitle">筛选内可交付 ${filteredStats.delivery} 题 · 单题可直接下载</div>
        </div>
        <span class="pill">Web ${stats.categories.web} · Pwn ${stats.categories.pwn} · RE ${stats.categories.re}</span>
      </div>
      <div class="filter-bar-responsive ch-filter-bar">
        <div class="filter-buttons">
          ${filterButton("all", "全部", stats.total)}
          ${CATEGORIES.map((category) => filterButton(
            category,
            categoryLabel(category),
            stats.categories[category],
          )).join("")}
        </div>
        <div class="input-icon-wrapper">
          <i data-lucide="search" class="input-icon-left"></i>
          <input id="challengeSearch" value="${escapeHtml(appState.search)}" class="input" placeholder="搜索题目">
        </div>
      </div>
      <div class="table-container ch-table-wrap">
        <table class="table ch-table">
          <thead>
            <tr>
              <th>题目</th>
              <th>类别</th>
          <th>难度</th>
          <th>技术栈</th>
              <th>构建</th>
              <th>EXP</th>
              <th>交付</th>
              <th>操作</th>
              <th>更新</th>
            </tr>
          </thead>
          <tbody>${rowsHtml(filtered)}</tbody>
        </table>
      </div>
      <div class="ch-card-list">${cardsHtml(filtered)}</div>
    </section>
  `;
}

export function bind() {
  document.addEventListener("click", (event) => {
    const downloadButton = event.target.closest("#challengeDeliveryDownload");
    if (downloadButton) {
      downloadDelivery();
      return;
    }
    const singleDownload = event.target.closest(".ch-download-btn");
    if (singleDownload) {
      downloadSingleDelivery(singleDownload.dataset.challengeId);
      return;
    }
    const button = event.target.closest(".filter-button");
    if (!button) return;
    appState.category = button.dataset.category;
    render(appState.data);
    initIcons();
  });

  document.addEventListener("input", (event) => {
    if (event.target?.id !== "challengeSearch") return;
    appState.search = event.target.value;
    render(appState.data);
    initIcons();
    document.querySelector("#challengeSearch")?.focus();
  });
}

export function activate() {
  appState.category = "all";
}
