import { api } from "../api.js";
import { escapeHtml, categoryLabel, categoryTone, statusIndicator, softPill } from "../ui/format.js";

const RUN_STATUSES = ["queued", "running", "completed", "failed"];

const state = {
  runs: null,
  flags: {},
  filter: { status: "", claimed_by: "" },
};

async function ensureRuns() {
  if (state.runs !== null) return;
  if (state.flags.runs?.loading) return;
  state.flags.runs = { loading: true, error: null };
  try {
    const url = buildRunsUrl();
    state.runs = await api(url);
    state.flags.runs = { loading: false, error: null };
  } catch (err) {
    state.flags.runs = { loading: false, error: err.message };
  }
  render(state.data);
}

function buildRunsUrl() {
  const p = new URLSearchParams();
  if (state.filter.status) p.set("status", state.filter.status);
  if (state.filter.claimed_by) p.set("claimed_by", state.filter.claimed_by);
  return p.toString() ? `/api/research/runs?${p}` : "/api/research/runs";
}

async function forceReloadRuns() {
  state.runs = null;
  state.flags.runs = { loading: false, error: null };
  await ensureRuns();
}

export function render(data) {
  state.data = data;
  ensureRuns();
  const root = document.querySelector('[data-view="research-runs"]');
  if (!root) return;

  const flag = state.flags.runs || {};
  if (flag.loading && !state.runs) {
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

  const items = state.runs || [];

  root.innerHTML = `
    <section class="card">
      <div class="card-header">
        <div>
          <div class="card-title">运行记录</div>
          <div class="card-subtitle">research_runs 全表；按 status / claimed_by 过滤</div>
        </div>
        <span class="pill">${items.length} 项</span>
      </div>
      <div class="filter-bar">
        <label class="filter-item">状态
          <select id="run-filter-status" class="filter-select">
            <option value=""${state.filter.status === "" ? " selected" : ""}>全部</option>
            ${RUN_STATUSES.map(s => `<option value="${escapeHtml(s)}"${state.filter.status === s ? " selected" : ""}>${escapeHtml(s)}</option>`).join("")}
          </select>
        </label>
        <label class="filter-item">claimed_by
          <input id="run-filter-claimed-by" value="${escapeHtml(state.filter.claimed_by)}" class="filter-input" placeholder="worker id" style="width: 176px;" />
        </label>
        <button id="run-clear-filter" class="filter-clear">清空</button>
      </div>
      ${items.length ? renderRunsTable(items) : `<div class="empty card-body">没有匹配的运行</div>`}
    </section>
  `;
}

function renderRunsTable(items) {
  return `
    <div class="table-container">
      <table class="table">
        <thead>
          <tr>
            <th>Run ID</th>
            <th>类别</th>
            <th>尝试</th>
            <th>状态</th>
            <th>Worker</th>
            <th>profile</th>
            <th>started_at</th>
          </tr>
        </thead>
        <tbody>
          ${items.map(r => `
            <tr>
              <td class="table-cell-id">${escapeHtml(r.id.slice(0, 8))}…</td>
              <td>${r.category ? softPill(categoryLabel(r.category), categoryTone(r.category)) : "-"}</td>
              <td style="text-align: center;">${r.attempt}</td>
              <td>${statusIndicator(r.status)}</td>
              <td class="mono" style="font-size: var(--font-mono);">${escapeHtml(r.claimed_by || "-")}</td>
              <td class="mono" style="font-size: var(--font-mono);">${escapeHtml(r.profile_name_used || "-")}</td>
              <td class="table-cell-time">${escapeHtml(r.started_at?.slice(0, 19) || "-")}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

export function bind() {
  document.addEventListener("click", (e) => {
    const root = document.querySelector('[data-view="research-runs"]');
    if (!root || !root.contains(e.target)) return;

    if (e.target.closest("#run-clear-filter")) {
      state.filter = { status: "", claimed_by: "" };
      forceReloadRuns();
    }
  });

  document.addEventListener("change", (e) => {
    const root = document.querySelector('[data-view="research-runs"]');
    if (!root || !root.contains(e.target)) return;

    if (e.target.id === "run-filter-status") {
      state.filter.status = e.target.value;
      forceReloadRuns();
    }
  });

  let timer;
  document.addEventListener("input", (e) => {
    const root = document.querySelector('[data-view="research-runs"]');
    if (!root || !root.contains(e.target)) return;

    if (e.target.id === "run-filter-claimed-by") {
      clearTimeout(timer);
      const value = e.target.value;
      timer = setTimeout(() => {
        state.filter.claimed_by = value;
        forceReloadRuns();
      }, 300);
    }
  });
}