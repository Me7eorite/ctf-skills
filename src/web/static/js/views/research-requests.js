import { api } from "../api.js";
import { showToast } from "../ui/toast.js";
import { escapeHtml, categoryLabel, categoryTone, statusIndicator, softPill } from "../ui/format.js";
import { setView } from "../router.js";

const REQUEST_STATUSES = ["draft", "researching", "researched", "failed"];

const state = {
  requests: null,
  categories: null,
  detail: null,
  detailId: null,
  flags: {},
  filter: { category: "", status: "" },
};

async function ensureRequests() {
  if (state.requests !== null) return;
  if (state.flags.requests?.loading) return;
  state.flags.requests = { loading: true, error: null };
  try {
    const url = buildRequestsUrl();
    state.requests = await api(url);
    state.flags.requests = { loading: false, error: null };
  } catch (err) {
    state.flags.requests = { loading: false, error: err.message };
  }
  render(state.data);
}

async function ensureCategories() {
  if (state.categories !== null) return;
  try {
    state.categories = await api("/api/research/categories");
  } catch (err) {
    state.categories = [];
  }
  render(state.data);
}

function buildRequestsUrl() {
  const p = new URLSearchParams();
  if (state.filter.category) p.set("category", state.filter.category);
  if (state.filter.status) p.set("status", state.filter.status);
  return p.toString() ? `/api/research/requests?${p}` : "/api/research/requests";
}

async function fetchDetail(id) {
  if (state.detail !== null) return;
  state.flags.detail = { loading: true };
  try {
    state.detail = await api(`/api/research/requests/${id}`);
    state.flags.detail = { loading: false };
    render(state.data);
    window.lucide?.createIcons();
  } catch (err) {
    showToast(err.message, true);
    state.flags.detail = { loading: false };
    state.detailId = null;
    state.detail = null;
    render(state.data);
  }
}

async function forceReloadRequests() {
  state.requests = null;
  state.flags.requests = { loading: false, error: null };
  await ensureRequests();
}

export function render(data) {
  state.data = data;
  ensureRequests();
  ensureCategories();
  const root = document.querySelector('[data-view="research-requests"]');
  if (!root) return;

  const flag = state.flags.requests || {};
  if (flag.loading && !state.requests) {
    root.innerHTML = `<div class="empty">加载中…</div>`;
    return;
  }
  if (flag.error) {
    root.innerHTML = `
      <div style="border-radius: var(--radius-md); border: 1px solid var(--accent-red-border); background: var(--accent-red-light); padding: var(--space-md);">
        <div style="font-weight: 500;">加载失败</div>
        <p style="font-size: var(--font-md);">${escapeHtml(flag.error)}</p>
      </div>
    `;
    return;
  }

  if (state.detailId) {
    renderDetail(root);
    return;
  }

  const items = state.requests || [];
  const cats = state.categories || [];

  root.innerHTML = `
    <section class="card">
      <div class="card-header">
        <div>
          <div class="card-title">需求管理</div>
          <div class="card-subtitle">点击行查看详情（含所有 run / sources / findings）</div>
        </div>
        <span class="pill">${items.length} 项</span>
      </div>
      <div class="filter-bar">
        <label class="filter-item">类别
          <select id="req-filter-cat" class="filter-select">
            <option value=""${state.filter.category === "" ? " selected" : ""}>全部</option>
            ${cats.map(c => `<option value="${escapeHtml(c.code)}"${state.filter.category === c.code ? " selected" : ""}>${escapeHtml(c.code)}</option>`).join("")}
          </select>
        </label>
        <label class="filter-item">状态
          <select id="req-filter-status" class="filter-select">
            <option value=""${state.filter.status === "" ? " selected" : ""}>全部</option>
            ${REQUEST_STATUSES.map(s => `<option value="${escapeHtml(s)}"${state.filter.status === s ? " selected" : ""}>${escapeHtml(s)}</option>`).join("")}
          </select>
        </label>
        <button id="req-clear-filter" class="filter-clear">清空</button>
      </div>
      ${items.length ? renderRequestsTable(items) : `<div class="empty card-body">没有匹配的请求</div>`}
    </section>
  `;
}

function renderRequestsTable(items) {
  return `
    <div class="table-container">
      <table class="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>类别</th>
            <th>话题</th>
            <th>目标</th>
            <th>状态</th>
            <th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          ${items.map(r => `
            <tr class="table-row-clickable" data-id="${escapeHtml(r.id)}">
              <td class="table-cell-id">${escapeHtml(r.id.slice(0, 8))}…</td>
              <td>${softPill(categoryLabel(r.category), categoryTone(r.category))}</td>
              <td><div class="truncate" style="max-width: 280px;">${escapeHtml(r.topic)}</div></td>
              <td style="text-align: right;">${r.target_count}</td>
              <td>${statusIndicator(r.status)}</td>
              <td class="table-cell-time">${escapeHtml(r.created_at?.slice(0, 19) ?? "-")}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderDetail(root) {
  if (state.detail === null) {
    fetchDetail(state.detailId);
    root.innerHTML = `<div class="empty">加载详情中…</div>`;
    return;
  }

  const { request, runs = [], sources = [], findings_by_kind = {} } = state.detail;

  root.innerHTML = `
    <button class="btn btn-ghost" id="research-back" style="margin-bottom: var(--space-md);">
      <i data-lucide="arrow-left"></i> 返回列表
    </button>

    <section class="card card-body">
      <div class="flex items-center gap-2">
        ${softPill(categoryLabel(request.category), categoryTone(request.category))}
        ${statusIndicator(request.status)}
      </div>
      <h2 style="font-size: var(--font-lg); font-weight: 600; margin-top: var(--space-sm);">${escapeHtml(request.topic)}</h2>
      <div class="mono" style="font-size: var(--font-sm); color: var(--ink-500); margin-top: 2px;">${escapeHtml(request.id)}</div>
      <dl style="margin-top: var(--space-lg); display: grid; gap: var(--space-md);">
        <div>
          <dt style="font-size: var(--font-xs); color: var(--ink-400); text-transform: uppercase;">target_count</dt>
          <dd style="font-size: var(--font-md); color: var(--ink-700);">${request.target_count}</dd>
        </div>
        <div>
          <dt style="font-size: var(--font-xs); color: var(--ink-400); text-transform: uppercase;">difficulty</dt>
          <dd style="font-size: var(--font-md); color: var(--ink-700);">${Object.entries(request.difficulty_distribution || {}).map(([k, v]) => `${k}=${v}`).join(", ") || "-"}</dd>
        </div>
      </dl>
    </section>

    <section class="card" style="margin-top: var(--space-lg);">
      <div class="card-header">
        <div><div class="card-title">所有运行</div></div>
        <span class="pill">${runs.length}</span>
      </div>
      ${runs.length ? renderRunsTable(runs) : `<div class="empty card-body">尚无运行</div>`}
    </section>

    ${sources.length ? `
      <section class="card" style="margin-top: var(--space-lg);">
        <div class="card-header">
          <div><div class="card-title">Sources</div></div>
          <span class="pill">${sources.length}</span>
        </div>
        <div style="border-top: 1px solid var(--line);">
          ${sources.map(s => `
            <div style="padding: var(--space-md);">
              <a href="${escapeHtml(s.url)}" target="_blank" style="font-size: var(--font-md); font-weight: 500; color: var(--brand-600);">${escapeHtml(s.title || s.url)}</a>
              <p style="font-size: var(--font-md); color: var(--ink-600); margin-top: 2px;">${escapeHtml(s.summary || "")}</p>
            </div>
          `).join("")}
        </div>
      </section>
    ` : ""}
  `;
}

function renderRunsTable(runs) {
  return `
    <div class="table-container">
      <table class="table">
        <thead>
          <tr>
            <th>尝试</th>
            <th>状态</th>
            <th>Worker</th>
            <th>finished_at</th>
          </tr>
        </thead>
        <tbody>
          ${runs.map(run => `
            <tr>
              <td style="text-align: center;">${run.attempt}</td>
              <td>${statusIndicator(run.status)}</td>
              <td class="mono" style="font-size: var(--font-mono);">${escapeHtml(run.claimed_by || "-")}</td>
              <td class="table-cell-time">${escapeHtml(run.finished_at?.slice(0, 19) || "-")}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

export function bind() {
  document.addEventListener("click", (e) => {
    const root = document.querySelector('[data-view="research-requests"]');
    if (!root || !root.contains(e.target)) return;

    if (e.target.closest("#research-back")) {
      state.detailId = null;
      state.detail = null;
      render(state.data);
      window.lucide?.createIcons();
      return;
    }

    if (e.target.closest("#req-clear-filter")) {
      state.filter = { category: "", status: "" };
      forceReloadRequests();
      return;
    }

    const row = e.target.closest(".table-row-clickable");
    if (row) {
      state.detailId = row.dataset.id;
      state.detail = null;
      render(state.data);
      window.lucide?.createIcons();
    }
  });

  document.addEventListener("change", (e) => {
    const root = document.querySelector('[data-view="research-requests"]');
    if (!root || !root.contains(e.target)) return;

    if (e.target.id === "req-filter-cat") {
      state.filter.category = e.target.value;
      forceReloadRequests();
    } else if (e.target.id === "req-filter-status") {
      state.filter.status = e.target.value;
      forceReloadRequests();
    }
  });
}