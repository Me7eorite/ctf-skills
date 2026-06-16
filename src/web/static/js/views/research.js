// Research view — wires the 7 read-only endpoints from
// `add-research-planning-core` Section 10 into the dashboard.
//
//   GET /api/research/categories                       (10.4)
//   GET /api/research/requests?category=&status=       (10.1)
//   GET /api/research/requests/{id}                    (10.2)
//   GET /api/research/runs?status=&...                 (10.7)
//   GET /api/research/queue/stats                      (10.8)
//   GET /api/profile/bindings                          (10.5)
//   GET /api/profile/bindings/{role}                   (10.6)

import { api } from "../api.js";
import {
  escapeHtml,
  categoryLabel,
  categoryTone,
  statusIndicator,
  softPill,
} from "../ui/format.js";
import { showToast } from "../ui/toast.js";

const state = {
  loaded: false,
  loading: false,
  error: null,
  mode: "list",            // "list" | "detail"
  detail: null,            // { request, runs, latest_run, sources, findings_by_kind }
  categories: [],
  stats: null,
  requests: [],
  runs: [],
  bindings: [],
  filter: { category: "", status: "" },
  runFilter: { status: "", claimed_by: "" },
};

const REQUEST_STATUSES = ["draft", "researching", "researched", "failed"];
const RUN_STATUSES = ["queued", "running", "completed", "failed"];

// ---------------------------------------------------------------------------
// data fetching
// ---------------------------------------------------------------------------

async function loadAll() {
  state.loading = true;
  state.error = null;
  try {
    const [stats, categories, bindings, requests, runs] = await Promise.all([
      api("/api/research/queue/stats"),
      api("/api/research/categories"),
      api("/api/profile/bindings"),
      api(buildRequestsQuery()),
      api(buildRunsQuery()),
    ]);
    state.stats = stats;
    state.categories = categories;
    state.bindings = bindings;
    state.requests = requests;
    state.runs = runs;
    state.loaded = true;
  } catch (err) {
    state.error = err.message || String(err);
  } finally {
    state.loading = false;
    paint();
  }
}

async function reloadRequests() {
  try {
    state.requests = await api(buildRequestsQuery());
    paint();
  } catch (err) {
    showToast(err.message, true);
  }
}

async function reloadRuns() {
  try {
    state.runs = await api(buildRunsQuery());
    paint();
  } catch (err) {
    showToast(err.message, true);
  }
}

async function loadDetail(requestId) {
  state.mode = "detail";
  state.detail = null;
  paint();
  try {
    state.detail = await api(`/api/research/requests/${requestId}`);
    paint();
  } catch (err) {
    showToast(err.message, true);
    state.mode = "list";
    paint();
  }
}

function buildRequestsQuery() {
  const params = new URLSearchParams();
  if (state.filter.category) params.set("category", state.filter.category);
  if (state.filter.status) params.set("status", state.filter.status);
  const qs = params.toString();
  return qs ? `/api/research/requests?${qs}` : "/api/research/requests";
}

function buildRunsQuery() {
  const params = new URLSearchParams();
  if (state.runFilter.status) params.set("status", state.runFilter.status);
  if (state.runFilter.claimed_by) params.set("claimed_by", state.runFilter.claimed_by);
  const qs = params.toString();
  return qs ? `/api/research/runs?${qs}` : "/api/research/runs";
}

// ---------------------------------------------------------------------------
// render helpers
// ---------------------------------------------------------------------------

function root() {
  return document.querySelector('[data-view="research"]');
}

function paint() {
  const node = root();
  if (!node) return;
  if (state.mode === "detail") {
    node.innerHTML = renderDetail();
  } else {
    node.innerHTML = renderList();
  }
  window.lucide?.createIcons();
}

function renderList() {
  if (!state.loaded && state.loading) return renderLoading();
  if (state.error) return renderError();
  return `
    ${renderStats()}
    <div class="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
      <section class="card">
        ${cardHeader("生成请求", "可按 category / status 过滤，点击行查看详情")}
        <div class="border-b border-line px-5 py-3 flex flex-wrap items-center gap-2">
          ${categoryFilter("filter-cat")}
          ${statusFilter("filter-status", REQUEST_STATUSES)}
          <button id="research-clear-filter"
            class="ml-auto text-[12px] text-ink-500 hover:text-ink-900">清空</button>
        </div>
        ${renderRequestsTable()}
      </section>
      <section class="card">
        ${cardHeader("Profile 绑定", "research/planning/shard_execution → Hermes profile")}
        ${renderBindings()}
      </section>
    </div>
    <section class="card mt-5">
      ${cardHeader("运行记录", "research_runs，按 status / claimed_by 过滤")}
      <div class="border-b border-line px-5 py-3 flex flex-wrap items-center gap-2">
        ${statusFilter("filter-run-status", RUN_STATUSES)}
        <label class="flex items-center gap-1.5 text-[12px] text-ink-500">claimed_by
          <input id="filter-claimed-by" value="${escapeHtml(state.runFilter.claimed_by)}"
            class="h-8 w-40 rounded-md border border-line-strong bg-surface px-2 text-[12px]
                   outline-none focus:border-ink-700" placeholder="worker id"/>
        </label>
        <button id="research-clear-run-filter"
          class="ml-auto text-[12px] text-ink-500 hover:text-ink-900">清空</button>
      </div>
      ${renderRunsTable()}
    </section>
  `;
}

function renderLoading() {
  return `
    <div class="grid gap-5 xl:grid-cols-4">
      ${Array.from({ length: 4 }, () => `
        <div class="card p-5 animate-pulse">
          <div class="h-3 w-16 bg-ink-100 rounded"></div>
          <div class="mt-3 h-7 w-12 bg-ink-100 rounded"></div>
        </div>
      `).join("")}
    </div>
    <div class="empty mt-5">正在加载研究数据…</div>
  `;
}

function renderError() {
  return `
    <div class="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-[13px] text-rose-700">
      <div class="font-medium">研究数据加载失败</div>
      <p class="mt-0.5 text-[12px]">${escapeHtml(state.error)}</p>
      <button id="research-retry"
        class="mt-3 inline-flex h-8 items-center rounded-md border border-rose-300 px-3 text-[12px] font-medium hover:bg-rose-100">
        重试
      </button>
    </div>
  `;
}

function renderStats() {
  const s = state.stats || {};
  const nearExpiry = s.runs_near_lease_expiry || [];
  const items = [
    ["queued", s.queued ?? 0, "排队", "list"],
    ["running", s.running ?? 0, "运行中", "loader"],
    ["completed", s.completed ?? 0, "已完成", "check-check"],
    ["failed", s.failed ?? 0, "失败", "alert-triangle"],
  ];
  return `
    <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      ${items.map(([key, value, label, icon]) => `
        <article class="card p-5">
          <div class="flex items-center justify-between text-[11px] font-medium text-ink-500">
            <span>${label}</span><i data-lucide="${icon}" class="size-4 text-ink-400"></i>
          </div>
          <div class="mt-4 text-3xl font-semibold tabular-nums">${value}</div>
          <div class="mt-2 text-[11px] text-ink-500">${
            key === "queued" && s.oldest_queued_age_seconds != null
              ? `最旧已等 ${Math.round(s.oldest_queued_age_seconds)}s`
              : key === "running"
              ? `近 60s 内到期 ${nearExpiry.length}`
              : "&nbsp;"
          }</div>
        </article>
      `).join("")}
    </div>
    ${nearExpiry.length ? `
      <div class="mt-3 rounded-md border border-amber-300 bg-amber-50 px-4 py-2.5 text-[12px] text-amber-800">
        <span class="font-medium">⚠ 租约即将过期：</span>
        ${nearExpiry.slice(0, 6).map(id => `<code class="font-mono text-[11px]">${escapeHtml(id.slice(0, 8))}…</code>`).join("&nbsp; ")}
        ${nearExpiry.length > 6 ? `<span>… 共 ${nearExpiry.length} 个</span>` : ""}
      </div>
    ` : ""}
  `;
}

function renderRequestsTable() {
  if (!state.requests.length) {
    return `<div class="empty m-5">没有匹配的生成请求</div>`;
  }
  return `
    <div class="overflow-x-auto">
      <table class="w-full min-w-[720px] text-left">
        <thead class="border-b border-line text-[11px] font-medium text-ink-500">
          <tr>
            <th class="px-4 py-3 font-medium">ID</th>
            <th class="px-4 py-3 font-medium">类别</th>
            <th class="px-4 py-3 font-medium">话题</th>
            <th class="px-4 py-3 font-medium">目标</th>
            <th class="px-4 py-3 font-medium">状态</th>
            <th class="px-4 py-3 font-medium">创建时间</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-line text-[13px]">
          ${state.requests.map(r => `
            <tr class="hover:bg-ink-50 cursor-pointer research-request-row" data-id="${escapeHtml(r.id)}">
              <td class="px-4 py-3 font-mono text-[11px] text-ink-500">${escapeHtml(r.id.slice(0, 8))}…</td>
              <td class="px-4 py-3">${softPill(categoryLabel(r.category), categoryTone(r.category))}</td>
              <td class="px-4 py-3"><div class="max-w-[260px] truncate">${escapeHtml(r.topic)}</div></td>
              <td class="px-4 py-3 tabular-nums">${r.target_count}</td>
              <td class="px-4 py-3">${statusIndicator(r.status)}</td>
              <td class="px-4 py-3 text-[11px] text-ink-500 tabular-nums">${escapeHtml(r.created_at?.slice(0, 19) ?? "-")}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderRunsTable() {
  if (!state.runs.length) {
    return `<div class="empty m-5">没有匹配的运行记录</div>`;
  }
  return `
    <div class="overflow-x-auto">
      <table class="w-full min-w-[820px] text-left">
        <thead class="border-b border-line text-[11px] font-medium text-ink-500">
          <tr>
            <th class="px-4 py-3 font-medium">Run ID</th>
            <th class="px-4 py-3 font-medium">类别</th>
            <th class="px-4 py-3 font-medium">尝试</th>
            <th class="px-4 py-3 font-medium">状态</th>
            <th class="px-4 py-3 font-medium">Worker</th>
            <th class="px-4 py-3 font-medium">started_at</th>
            <th class="px-4 py-3 font-medium">last_error</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-line text-[13px]">
          ${state.runs.map(r => `
            <tr class="hover:bg-ink-50">
              <td class="px-4 py-3 font-mono text-[11px] text-ink-500">${escapeHtml(r.id.slice(0, 8))}…</td>
              <td class="px-4 py-3">${r.category ? softPill(categoryLabel(r.category), categoryTone(r.category)) : "-"}</td>
              <td class="px-4 py-3 tabular-nums">${r.attempt}</td>
              <td class="px-4 py-3">${statusIndicator(r.status)}</td>
              <td class="px-4 py-3 text-[12px] font-mono">${escapeHtml(r.claimed_by || "-")}</td>
              <td class="px-4 py-3 text-[11px] text-ink-500 tabular-nums">${escapeHtml(r.started_at?.slice(0, 19) || "-")}</td>
              <td class="px-4 py-3"><div class="max-w-[280px] truncate text-[12px] text-ink-600">${escapeHtml(r.last_error || "")}</div></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderBindings() {
  if (!state.bindings.length) {
    return `<div class="empty m-5">没有 Hermes profile 绑定</div>`;
  }
  return `
    <div>
      ${state.bindings.map(b => `
        <div class="border-b border-line px-5 py-4 last:border-b-0">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="flex items-center gap-2">
                <span class="text-[13px] font-semibold">${escapeHtml(b.role)}</span>
                <span class="text-[11px] text-ink-500">${escapeHtml(b.display_name || "")}</span>
              </div>
              <div class="mt-1.5 text-[12px] text-ink-600">
                profile: <code class="font-mono text-[11px] bg-ink-100 px-1.5 py-0.5 rounded">${escapeHtml(b.profile_name)}</code>
              </div>
              ${b.description ? `<p class="mt-1 text-[11px] text-ink-500">${escapeHtml(b.description)}</p>` : ""}
            </div>
            <div class="text-right shrink-0">
              ${statusIndicator(b.status)}
              <div class="mt-1 text-[11px] text-ink-500 tabular-nums">${escapeHtml(b.last_used_at?.slice(0, 19) || "未使用")}</div>
            </div>
          </div>
        </div>
      `).join("")}
    </div>
  `;
}

// ---------------------------------------------------------------------------
// detail view
// ---------------------------------------------------------------------------

function renderDetail() {
  if (!state.detail) {
    return `
      <button class="text-[13px] text-brand-600 mb-4 inline-flex items-center gap-1" id="research-back">
        <i data-lucide="arrow-left" class="size-4"></i> 返回列表
      </button>
      <div class="empty">正在加载详情…</div>
    `;
  }
  const { request, latest_run, runs = [], sources = [], findings_by_kind = {} } = state.detail;
  return `
    <button class="text-[13px] text-brand-600 mb-4 inline-flex items-center gap-1" id="research-back">
      <i data-lucide="arrow-left" class="size-4"></i> 返回列表
    </button>

    <section class="card p-5">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <div class="flex items-center gap-2">
            ${softPill(categoryLabel(request.category), categoryTone(request.category))}
            ${statusIndicator(request.status)}
          </div>
          <h2 class="mt-2 text-[15px] font-semibold">${escapeHtml(request.topic)}</h2>
          <div class="mt-1 font-mono text-[11px] text-ink-500">${escapeHtml(request.id)}</div>
        </div>
      </div>
      <dl class="mt-5 grid gap-3 text-[12px] sm:grid-cols-2">
        ${kv("target_count", String(request.target_count))}
        ${kv("max_attempts", String(request.max_attempts))}
        ${kv("difficulty", Object.entries(request.difficulty_distribution || {}).map(([k, v]) => `${k}=${v}`).join(", ") || "-")}
        ${kv("seed_urls", (request.seed_urls || []).length ? request.seed_urls.map(u => `<code class="font-mono text-[11px]">${escapeHtml(u)}</code>`).join("<br>") : "(none)")}
        ${kv("created_at", escapeHtml(request.created_at?.slice(0, 19) || "-"))}
        ${kv("updated_at", escapeHtml(request.updated_at?.slice(0, 19) || "-"))}
      </dl>
    </section>

    <section class="card mt-5">
      ${cardHeader(`所有运行（${runs.length}）`, "按 attempt 升序；最新一次会有 sources/findings")}
      ${runs.length ? `
        <div class="overflow-x-auto">
          <table class="w-full min-w-[760px] text-left">
            <thead class="border-b border-line text-[11px] font-medium text-ink-500">
              <tr>
                <th class="px-4 py-3 font-medium">尝试</th>
                <th class="px-4 py-3 font-medium">状态</th>
                <th class="px-4 py-3 font-medium">Worker</th>
                <th class="px-4 py-3 font-medium">profile</th>
                <th class="px-4 py-3 font-medium">finished_at</th>
                <th class="px-4 py-3 font-medium">last_error</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-line text-[13px]">
              ${runs.map(run => `
                <tr class="${latest_run && run.id === latest_run.id ? "bg-brand-50/40" : ""}">
                  <td class="px-4 py-3 tabular-nums">${run.attempt}</td>
                  <td class="px-4 py-3">${statusIndicator(run.status)}</td>
                  <td class="px-4 py-3 font-mono text-[12px]">${escapeHtml(run.claimed_by || "-")}</td>
                  <td class="px-4 py-3 font-mono text-[12px]">${escapeHtml(run.profile_name_used || "-")}</td>
                  <td class="px-4 py-3 text-[11px] text-ink-500 tabular-nums">${escapeHtml(run.finished_at?.slice(0, 19) || "-")}</td>
                  <td class="px-4 py-3"><div class="max-w-[280px] truncate text-[12px] text-ink-600">${escapeHtml(run.last_error || "")}</div></td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      ` : `<div class="empty m-5">尚无运行</div>`}
    </section>

    ${sources.length || Object.keys(findings_by_kind).length ? `
      <div class="grid gap-5 xl:grid-cols-2 mt-5">
        <section class="card">
          ${cardHeader(`Sources（${sources.length}）`, "最新一次 run 采集的引用")}
          ${sources.length ? `
            <div class="divide-y divide-line">
              ${sources.map(s => `
                <div class="px-5 py-3.5">
                  <a href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer"
                    class="text-[13px] font-medium text-brand-600 hover:text-brand-700 break-all">${escapeHtml(s.title || s.url)}</a>
                  <p class="mt-1 text-[12px] text-ink-600">${escapeHtml(s.summary || "")}</p>
                  ${s.raw_text_path ? `<div class="mt-1.5 text-[11px] text-ink-500 font-mono">${escapeHtml(s.raw_text_path)}</div>` : ""}
                </div>
              `).join("")}
            </div>
          ` : `<div class="empty m-5">无 sources</div>`}
        </section>
        <section class="card">
          ${cardHeader("Findings", "按 kind 分组")}
          ${Object.keys(findings_by_kind).length ? `
            <div class="divide-y divide-line">
              ${Object.entries(findings_by_kind).map(([kind, items]) => `
                <div class="px-5 py-4">
                  <div class="flex items-center gap-2 mb-2">
                    ${softPill(kind, "text-ink-700 bg-ink-100")}
                    <span class="text-[11px] text-ink-500">${items.length} 项</span>
                  </div>
                  <div class="space-y-2">
                    ${items.map(f => `
                      <div>
                        <div class="text-[13px] font-medium">${escapeHtml(f.label)}</div>
                        <p class="mt-0.5 text-[12px] text-ink-600">${escapeHtml(f.summary || "")}</p>
                      </div>
                    `).join("")}
                  </div>
                </div>
              `).join("")}
            </div>
          ` : `<div class="empty m-5">无 findings</div>`}
        </section>
      </div>
    ` : ""}
  `;
}

function kv(key, value) {
  return `
    <div>
      <dt class="text-[10px] uppercase tracking-wider text-ink-400 mb-0.5">${escapeHtml(key)}</dt>
      <dd class="text-[12px] text-ink-700">${value}</dd>
    </div>
  `;
}

function cardHeader(title, subtitle) {
  return `
    <div class="card-header">
      <div>
        <div class="card-title">${escapeHtml(title)}</div>
        <div class="card-subtitle">${escapeHtml(subtitle)}</div>
      </div>
    </div>
  `;
}

function categoryFilter(id) {
  const opts = state.categories.map(c =>
    `<option value="${escapeHtml(c.code)}"${state.filter.category === c.code ? " selected" : ""}>${escapeHtml(c.display_name || c.code)}</option>`
  ).join("");
  return `
    <label class="flex items-center gap-1.5 text-[12px] text-ink-500">类别
      <select id="${id}" class="h-8 rounded-md border border-line-strong bg-surface px-2 text-[12px] outline-none focus:border-ink-700">
        <option value=""${state.filter.category === "" ? " selected" : ""}>全部</option>
        ${opts}
      </select>
    </label>
  `;
}

function statusFilter(id, options) {
  const value = id === "filter-run-status" ? state.runFilter.status : state.filter.status;
  const opts = options.map(s =>
    `<option value="${escapeHtml(s)}"${value === s ? " selected" : ""}>${escapeHtml(s)}</option>`
  ).join("");
  return `
    <label class="flex items-center gap-1.5 text-[12px] text-ink-500">状态
      <select id="${id}" class="h-8 rounded-md border border-line-strong bg-surface px-2 text-[12px] outline-none focus:border-ink-700">
        <option value=""${value === "" ? " selected" : ""}>全部</option>
        ${opts}
      </select>
    </label>
  `;
}

// ---------------------------------------------------------------------------
// public hooks
// ---------------------------------------------------------------------------

export function render() {
  if (!state.loaded && !state.loading) {
    loadAll();
    paint();
    return;
  }
  paint();
}

export function bind() {
  // delegate via the document root since the view re-renders frequently.
  document.addEventListener("click", (e) => {
    if (!root() || root().contains(e.target) === false) return;

    if (e.target.closest("#research-back")) {
      state.mode = "list";
      state.detail = null;
      paint();
      return;
    }
    if (e.target.closest("#research-retry")) {
      loadAll();
      return;
    }
    if (e.target.closest("#research-clear-filter")) {
      state.filter = { category: "", status: "" };
      reloadRequests();
      return;
    }
    if (e.target.closest("#research-clear-run-filter")) {
      state.runFilter = { status: "", claimed_by: "" };
      reloadRuns();
      return;
    }
    const row = e.target.closest(".research-request-row");
    if (row) {
      loadDetail(row.dataset.id);
    }
  });

  document.addEventListener("change", (e) => {
    if (!root() || root().contains(e.target) === false) return;
    if (e.target.id === "filter-cat") {
      state.filter.category = e.target.value;
      reloadRequests();
    } else if (e.target.id === "filter-status") {
      state.filter.status = e.target.value;
      reloadRequests();
    } else if (e.target.id === "filter-run-status") {
      state.runFilter.status = e.target.value;
      reloadRuns();
    }
  });

  // debounce claimed_by free-text input.
  let runFilterTimer;
  document.addEventListener("input", (e) => {
    if (e.target.id !== "filter-claimed-by") return;
    clearTimeout(runFilterTimer);
    const value = e.target.value;
    runFilterTimer = setTimeout(() => {
      state.runFilter.claimed_by = value;
      reloadRuns();
    }, 300);
  });
}
