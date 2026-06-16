// Research view — tabbed sub-pages, ordered by the natural workflow.
// Each tab focuses on one piece of the pipeline so the screen never
// drowns the operator in everything at once.
//
//   1. 分类         GET  /api/research/categories
//   2. Profile 绑定 GET  /api/profile/bindings  (+ /{role})
//   3. 新建请求     POST /api/research/requests
//   4. 队列状态     GET  /api/research/queue/stats
//   5. 请求列表     GET  /api/research/requests   (+ /{id} for detail)
//   6. 运行记录     GET  /api/research/runs
//
// State is module-local; data for each tab loads lazily on first visit.

import { api, postJson } from "../api.js";
import { showToast } from "../ui/toast.js";
import {
  escapeHtml,
  categoryLabel,
  categoryTone,
  statusIndicator,
  softPill,
} from "../ui/format.js";

const TABS = [
  { key: "categories", label: "1. 分类",        endpoint: "GET /api/research/categories" },
  { key: "bindings",   label: "2. Profile 绑定", endpoint: "GET /api/profile/bindings" },
  { key: "submit",     label: "3. 新建请求",     endpoint: "POST /api/research/requests" },
  { key: "stats",      label: "4. 队列状态",     endpoint: "GET /api/research/queue/stats" },
  { key: "requests",   label: "5. 请求列表",     endpoint: "GET /api/research/requests" },
  { key: "runs",       label: "6. 运行记录",     endpoint: "GET /api/research/runs" },
];

const REQUEST_STATUSES = ["draft", "researching", "researched", "failed"];
const RUN_STATUSES = ["queued", "running", "completed", "failed"];
const DIFFICULTY_LABELS = ["easy", "medium", "hard", "expert"];

const state = {
  activeTab: "categories",
  detailId: null,         // when set: requests sub-tab is showing detail mode

  // per-tab data + flags
  categories: null,
  bindings: null,
  stats: null,
  requests: null,
  runs: null,
  detail: null,
  worker: null,           // /api/research/worker/status snapshot

  // per-tab loading / error flags (key → { loading, error })
  flags: {},

  // filters
  reqFilter: { category: "", status: "" },
  runFilter: { status: "", claimed_by: "" },

  // worker control form
  workerForm: {
    kind: "once",         // "once" | "loop"
    max_jobs: 1,
    lease_seconds: 900,
    hermes_timeout_seconds: 810,
    busy: false,
  },
  workerPollHandle: null,

  // submit form (kept across re-renders so the user doesn't lose their work)
  form: {
    category: "",
    topic: "",
    target_count: 5,
    seed_urls: "",
    max_attempts: 3,
    distribution: { easy: 0, medium: 0, hard: 0, expert: 0 },
    submitting: false,
    lastResult: null,
    lastError: null,
  },
};

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function root() {
  return document.querySelector('[data-view="research"]');
}

function setFlag(key, patch) {
  state.flags[key] = { ...(state.flags[key] || {}), ...patch };
}

function paint() {
  const node = root();
  if (!node) return;
  node.innerHTML = renderShell();
  window.lucide?.createIcons();
}

function tabsHeader() {
  return `
    <div class="card mb-5">
      <div class="flex overflow-x-auto">
        ${TABS.map(t => `
          <button class="research-tab flex-shrink-0 border-b-2 px-4 py-3 text-[13px] font-medium transition-colors ${
            state.activeTab === t.key
              ? "border-ink-900 text-ink-900 bg-ink-50/50"
              : "border-transparent text-ink-500 hover:text-ink-900 hover:bg-ink-50"
          }" data-tab="${t.key}">
            ${escapeHtml(t.label)}
          </button>
        `).join("")}
      </div>
      <div class="px-4 py-2 text-[11px] text-ink-400 font-mono border-t border-line">
        ${escapeHtml(TABS.find(t => t.key === state.activeTab)?.endpoint ?? "")}
      </div>
    </div>
  `;
}

function renderShell() {
  let body;
  switch (state.activeTab) {
    case "categories": body = renderCategoriesTab(); break;
    case "bindings":   body = renderBindingsTab(); break;
    case "submit":     body = renderSubmitTab(); break;
    case "stats":      body = renderStatsTab(); break;
    case "requests":   body = state.detailId ? renderDetailTab() : renderRequestsTab(); break;
    case "runs":       body = renderRunsTab(); break;
    default:           body = "";
  }
  return tabsHeader() + body;
}

function loadingPlaceholder(title) {
  return `<div class="empty">${escapeHtml(title)} 正在加载…</div>`;
}

function errorPlaceholder(message, retryKey) {
  return `
    <div class="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-[13px] text-rose-700">
      <div class="font-medium">加载失败</div>
      <p class="mt-0.5 text-[12px]">${escapeHtml(message)}</p>
      <button class="research-retry mt-3 inline-flex h-8 items-center rounded-md border border-rose-300 px-3 text-[12px] font-medium hover:bg-rose-100"
              data-key="${escapeHtml(retryKey)}">重试</button>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// data fetching — each tab loads what it needs on first visit
// ---------------------------------------------------------------------------

async function ensureLoaded(key, url, mutator = null) {
  // Idempotent — bail if data is already cached OR a fetch is already in flight.
  // The previous version called paint() before awaiting, which on first-render
  // re-entered renderXTab → ensureLoaded synchronously and recursed forever.
  if (state[key] !== null && state[key] !== undefined) return;
  if (state.flags[key]?.loading) return;
  setFlag(key, { loading: true, error: null });
  try {
    const data = await api(url);
    state[key] = mutator ? mutator(data) : data;
    setFlag(key, { loading: false, error: null });
  } catch (err) {
    setFlag(key, { loading: false, error: err.message || String(err) });
  }
  paint();
}

async function forceReload(key, url) {
  state[key] = null;
  setFlag(key, { loading: false, error: null });
  await ensureLoaded(key, url);
}

// ---------------------------------------------------------------------------
// Tab 1. 分类
// ---------------------------------------------------------------------------

function renderCategoriesTab() {
  ensureLoaded("categories", "/api/research/categories");
  const flag = state.flags.categories || {};
  if (flag.loading && !state.categories) return loadingPlaceholder("分类");
  if (flag.error) return errorPlaceholder(flag.error, "categories");
  const cats = state.categories || [];
  return `
    <section class="card">
      <div class="card-header">
        <div>
          <div class="card-title">题目分类</div>
          <div class="card-subtitle">submit / shard pipeline 共享的白名单；查看 challenge_categories</div>
        </div>
        <span class="pill">${cats.length} 项</span>
      </div>
      ${cats.length ? `
        <div class="overflow-x-auto">
          <table class="w-full min-w-[600px] text-left">
            <thead class="border-b border-line text-[11px] font-medium text-ink-500">
              <tr>
                <th class="px-5 py-3 font-medium">代码</th>
                <th class="px-5 py-3 font-medium">显示名</th>
                <th class="px-5 py-3 font-medium">描述</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-line text-[13px]">
              ${cats.map(c => `
                <tr>
                  <td class="px-5 py-3"><code class="font-mono text-[12px]">${escapeHtml(c.code)}</code></td>
                  <td class="px-5 py-3">${escapeHtml(c.display_name || "-")}</td>
                  <td class="px-5 py-3 text-ink-600">${escapeHtml(c.description || "-")}</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      ` : `<div class="empty m-5">没有分类</div>`}
    </section>
  `;
}

// ---------------------------------------------------------------------------
// Tab 2. Profile 绑定
// ---------------------------------------------------------------------------

function renderBindingsTab() {
  ensureLoaded("bindings", "/api/profile/bindings");
  const flag = state.flags.bindings || {};
  if (flag.loading && !state.bindings) return loadingPlaceholder("Profile 绑定");
  if (flag.error) return errorPlaceholder(flag.error, "bindings");
  const bindings = state.bindings || [];
  return `
    <section class="card">
      <div class="card-header">
        <div>
          <div class="card-title">Hermes Profile 绑定</div>
          <div class="card-subtitle">role → profile_name 映射；只读，写入用 CLI <code class="font-mono text-[11px]">profile bind</code></div>
        </div>
        <span class="pill">${bindings.length} 项</span>
      </div>
      ${bindings.length ? `
        <div class="divide-y divide-line">
          ${bindings.map(b => `
            <div class="px-5 py-4">
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0 flex-1">
                  <div class="flex items-center gap-2">
                    <span class="text-[14px] font-semibold">${escapeHtml(b.role)}</span>
                    <span class="text-[11px] text-ink-500">${escapeHtml(b.display_name || "")}</span>
                  </div>
                  <div class="mt-2 text-[12px] text-ink-600">
                    profile: <code class="font-mono text-[11px] bg-ink-100 px-1.5 py-0.5 rounded">${escapeHtml(b.profile_name)}</code>
                  </div>
                  ${b.description ? `<p class="mt-1.5 text-[11px] text-ink-500">${escapeHtml(b.description)}</p>` : ""}
                </div>
                <div class="text-right shrink-0">
                  ${statusIndicator(b.status)}
                  <div class="mt-1 text-[11px] text-ink-500 tabular-nums">
                    ${b.last_used_at ? `最近使用：${escapeHtml(b.last_used_at.slice(0, 19))}` : "尚未使用"}
                  </div>
                </div>
              </div>
            </div>
          `).join("")}
        </div>
      ` : `<div class="empty m-5">没有绑定</div>`}
    </section>
  `;
}

// ---------------------------------------------------------------------------
// Tab 3. 新建请求（form）
// ---------------------------------------------------------------------------

function renderSubmitTab() {
  // 中文注释：表单依赖 categories 给出 select；先确保它加载好。
  ensureLoaded("categories", "/api/research/categories");
  const cats = state.categories || [];
  const f = state.form;
  const distSum = Object.values(f.distribution).reduce((s, v) => s + (v || 0), 0);
  const matches = distSum === Number(f.target_count);
  return `
    <section class="card max-w-2xl">
      <div class="card-header">
        <div>
          <div class="card-title">新建研究请求</div>
          <div class="card-subtitle">提交后立即入队；Hermes 由 worker 异步执行</div>
        </div>
        <span class="pill">POST</span>
      </div>
      <form id="research-submit-form" class="p-5 grid gap-4">
        <label class="grid gap-1.5">
          <span class="label">类别 *</span>
          <select id="form-category" required class="select">
            <option value="">— 选择类别 —</option>
            ${cats.map(c => `
              <option value="${escapeHtml(c.code)}"${f.category === c.code ? " selected" : ""}>${escapeHtml(c.code)} · ${escapeHtml(c.display_name)}</option>
            `).join("")}
          </select>
        </label>

        <label class="grid gap-1.5">
          <span class="label">话题 *</span>
          <input id="form-topic" required value="${escapeHtml(f.topic)}"
            class="input" placeholder="SQL injection bypass via UNION select" />
        </label>

        <div class="grid grid-cols-2 gap-4">
          <label class="grid gap-1.5">
            <span class="label">目标数量 *</span>
            <input id="form-target-count" type="number" min="1" value="${f.target_count}" required class="input" />
          </label>
          <label class="grid gap-1.5">
            <span class="label">最大重试次数</span>
            <input id="form-max-attempts" type="number" min="1" value="${f.max_attempts}" class="input" />
          </label>
        </div>

        <fieldset class="grid gap-2">
          <div class="flex items-center justify-between">
            <span class="label">难度分布 *</span>
            <span class="text-[11px] ${matches ? "text-emerald-600" : "text-rose-600"}">
              合计 ${distSum} / 目标 ${f.target_count}${matches ? " ✓" : ""}
            </span>
          </div>
          <div class="grid grid-cols-4 gap-2">
            ${DIFFICULTY_LABELS.map(label => `
              <label class="grid gap-1">
                <span class="text-[10px] uppercase tracking-wider text-ink-500">${label}</span>
                <input data-difficulty="${label}" type="number" min="0" value="${f.distribution[label] || 0}" class="input text-center" />
              </label>
            `).join("")}
          </div>
        </fieldset>

        <label class="grid gap-1.5">
          <span class="label">种子 URL（可选，每行一个）</span>
          <textarea id="form-seed-urls" rows="3" placeholder="https://owasp.org/Top10/
https://portswigger.net/web-security"
            class="textarea font-mono text-[12px]">${escapeHtml(f.seed_urls)}</textarea>
        </label>

        ${f.lastResult ? `
          <div class="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-[12px] text-emerald-800">
            ✓ 已入队 · request_id <code class="font-mono">${escapeHtml(f.lastResult.request_id.slice(0, 8))}…</code> · run_id <code class="font-mono">${escapeHtml(f.lastResult.run_id.slice(0, 8))}…</code>
          </div>
        ` : ""}
        ${f.lastError ? `
          <div class="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-[12px] text-rose-800">
            ✗ ${escapeHtml(f.lastError)}
          </div>
        ` : ""}

        <div class="flex items-center gap-3 pt-2">
          <button type="submit" ${f.submitting ? "disabled" : ""} class="flex h-10 items-center gap-2 rounded-md bg-ink-900 px-4 text-[13px] font-medium text-white hover:bg-ink-800 disabled:opacity-50">
            ${f.submitting ? '<i data-lucide="loader" class="size-4 animate-spin"></i> 提交中…' : '<i data-lucide="send" class="size-4"></i> 提交'}
          </button>
          <button id="form-reset" type="button" class="h-10 rounded-md border border-line px-3 text-[13px] font-medium hover:bg-ink-50">
            重置
          </button>
        </div>
      </form>
    </section>
  `;
}

async function handleSubmit() {
  const f = state.form;
  const seedList = f.seed_urls
    .split("\n")
    .map(s => s.trim())
    .filter(s => s.length > 0);
  const dist = Object.fromEntries(
    Object.entries(f.distribution).filter(([_, v]) => Number(v) > 0)
  );

  f.submitting = true;
  f.lastResult = null;
  f.lastError = null;
  paint();
  try {
    const result = await postJson("/api/research/requests", {
      category: f.category,
      topic: f.topic,
      target_count: Number(f.target_count),
      difficulty_distribution: dist,
      seed_urls: seedList,
      max_attempts: Number(f.max_attempts),
    });
    f.lastResult = result;
    showToast(`已入队：${result.request_id.slice(0, 8)}…`);
    // 中文注释：让其它 tab 重新拉一次，确保用户能立刻看到新行。
    state.requests = null;
    state.runs = null;
    state.stats = null;
  } catch (err) {
    f.lastError = err.message || String(err);
  } finally {
    f.submitting = false;
    paint();
  }
}

function resetForm() {
  state.form = {
    category: "",
    topic: "",
    target_count: 5,
    seed_urls: "",
    max_attempts: 3,
    distribution: { easy: 0, medium: 0, hard: 0, expert: 0 },
    submitting: false,
    lastResult: null,
    lastError: null,
  };
  paint();
}

// ---------------------------------------------------------------------------
// Tab 4. 队列状态
// ---------------------------------------------------------------------------

function renderStatsTab() {
  ensureLoaded("stats", "/api/research/queue/stats");
  ensureWorkerStatus();
  const flag = state.flags.stats || {};
  if (flag.loading && !state.stats) return loadingPlaceholder("队列统计");
  if (flag.error) return errorPlaceholder(flag.error, "stats");
  const s = state.stats || {};
  const nearExpiry = s.runs_near_lease_expiry || [];
  const cells = [
    ["queued", s.queued ?? 0, "排队", "list",
     s.oldest_queued_age_seconds != null ? `最旧已等 ${Math.round(s.oldest_queued_age_seconds)}s` : ""],
    ["running", s.running ?? 0, "运行中", "loader",
     `近 60s 内到期 ${nearExpiry.length}`],
    ["completed", s.completed ?? 0, "已完成", "check-check", ""],
    ["failed", s.failed ?? 0, "失败", "alert-triangle", ""],
  ];
  return `
    <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      ${cells.map(([_, value, label, icon, note]) => `
        <article class="card p-5">
          <div class="flex items-center justify-between text-[11px] font-medium text-ink-500">
            <span>${label}</span><i data-lucide="${icon}" class="size-4 text-ink-400"></i>
          </div>
          <div class="mt-4 text-4xl font-semibold tabular-nums">${value}</div>
          <div class="mt-2 text-[11px] text-ink-500">${escapeHtml(note || " ")}</div>
        </article>
      `).join("")}
    </div>
    ${nearExpiry.length ? `
      <section class="card mt-5">
        <div class="card-header">
          <div>
            <div class="card-title">租约即将过期的 run</div>
            <div class="card-subtitle">running 且 lease_expires_at < now + 60s</div>
          </div>
          <span class="pill">${nearExpiry.length}</span>
        </div>
        <ul class="divide-y divide-line">
          ${nearExpiry.map(id => `
            <li class="px-5 py-2.5 font-mono text-[12px] text-ink-700">${escapeHtml(id)}</li>
          `).join("")}
        </ul>
      </section>
    ` : ""}

    ${renderWorkerControl()}

    <div class="mt-5 text-center">
      <button id="stats-refresh" class="inline-flex h-9 items-center gap-2 rounded-md border border-line px-3 text-[13px] font-medium hover:bg-ink-50">
        <i data-lucide="refresh-cw" class="size-4"></i> 刷新状态
      </button>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Worker control (bottom of the queue-stats tab)
// ---------------------------------------------------------------------------

function renderWorkerControl() {
  const w = state.worker;
  const form = state.workerForm;
  const running = w?.running;
  const available = w?.available !== false;
  const statusTone = running
    ? "dot-warn"
    : w?.returncode === 0
    ? "dot-ok"
    : w?.returncode != null
    ? "dot-err"
    : "dot-idle";
  return `
    <section class="card mt-5">
      <div class="card-header">
        <div>
          <div class="card-title">Research Worker</div>
          <div class="card-subtitle">触发后端 <code class="font-mono text-[11px]">cli research worker</code> 子进程，把队列里 queued 的任务跑起来</div>
        </div>
        <span class="inline-flex items-center text-[12px] text-ink-700">
          <span class="dot ${statusTone}"></span>
          ${running ? "运行中" : w?.returncode === 0 ? "已完成" : w?.returncode != null ? "已退出" : "空闲"}
        </span>
      </div>

      ${!available ? `
        <div class="p-5 text-[13px] text-ink-500">
          这个 dashboard 实例没配 worker 管理器，无法在 UI 触发。请用 <code class="font-mono text-[12px]">cli research worker</code>.
        </div>
      ` : `
        <div class="p-5 grid gap-4">
          ${running ? `
            <div class="rounded-md border border-amber-300 bg-amber-50 px-3 py-2.5 text-[12px] text-amber-800">
              ${escapeHtml(w.message || "worker 正在跑")} ·
              <code class="font-mono text-[11px]">${escapeHtml(w.kind || "")}</code> · 启动于 ${escapeHtml(w.started_at || "")}
              ${w.log_path ? `<div class="mt-1 text-[11px] text-amber-700">日志：${escapeHtml(w.log_path)}</div>` : ""}
            </div>
          ` : ""}
          ${(!running && w?.returncode != null) ? `
            <div class="rounded-md border ${w.returncode === 0 ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-rose-200 bg-rose-50 text-rose-800"} px-3 py-2.5 text-[12px]">
              ${escapeHtml(w.message || "")} ${w.log_path ? `<span class="ml-1">· 日志 <code class="font-mono text-[11px]">${escapeHtml(w.log_path)}</code></span>` : ""}
              ${w.log_tail ? `<pre class="mt-2 max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11px] leading-snug">${escapeHtml(w.log_tail)}</pre>` : ""}
            </div>
          ` : ""}

          <div class="grid gap-3 sm:grid-cols-4">
            <label class="grid gap-1">
              <span class="text-[10px] uppercase tracking-wider text-ink-500">模式</span>
              <select id="worker-kind" class="select" ${running ? "disabled" : ""}>
                <option value="once"${form.kind === "once" ? " selected" : ""}>跑一次</option>
                <option value="loop"${form.kind === "loop" ? " selected" : ""}>持续 loop</option>
              </select>
            </label>
            <label class="grid gap-1">
              <span class="text-[10px] uppercase tracking-wider text-ink-500">max_jobs</span>
              <input id="worker-max-jobs" type="number" min="1" value="${form.max_jobs}" ${form.kind === "loop" || running ? "disabled" : ""} class="input text-center" />
            </label>
            <label class="grid gap-1">
              <span class="text-[10px] uppercase tracking-wider text-ink-500">lease s</span>
              <input id="worker-lease" type="number" min="1" value="${form.lease_seconds}" ${running ? "disabled" : ""} class="input text-center" />
            </label>
            <label class="grid gap-1">
              <span class="text-[10px] uppercase tracking-wider text-ink-500">hermes timeout s</span>
              <input id="worker-hermes-timeout" type="number" min="1" value="${form.hermes_timeout_seconds}" ${running ? "disabled" : ""} class="input text-center" />
            </label>
          </div>

          <div class="flex items-center gap-2">
            ${running ? `
              <button id="worker-stop" ${form.busy ? "disabled" : ""}
                class="flex h-10 items-center gap-2 rounded-md bg-rose-600 px-4 text-[13px] font-medium text-white hover:bg-rose-700 disabled:opacity-50">
                <i data-lucide="square" class="size-4"></i> 停止 Worker
              </button>
            ` : `
              <button id="worker-start" ${form.busy ? "disabled" : ""}
                class="flex h-10 items-center gap-2 rounded-md bg-ink-900 px-4 text-[13px] font-medium text-white hover:bg-ink-800 disabled:opacity-50">
                ${form.busy ? '<i data-lucide="loader" class="size-4 animate-spin"></i> 请求中…' : '<i data-lucide="play" class="size-4"></i> ' + (form.kind === "loop" ? "持续运行" : "运行一次")}
              </button>
            `}
            <span class="text-[11px] text-ink-500">
              ${form.kind === "loop" ? "持续 loop：占据 dashboard 一个 worker 进程，直到点停止" : `跑一次：处理 ${form.max_jobs} 个 queued 任务后自动退出`}
            </span>
          </div>
        </div>
      `}
    </section>
  `;
}

async function ensureWorkerStatus() {
  if (state.worker !== null) return;
  await refreshWorkerStatus();
}

async function refreshWorkerStatus() {
  try {
    state.worker = await api("/api/research/worker/status");
  } catch (err) {
    state.worker = { available: false, running: false, error: err.message };
  }
  // While running, poll every 2s + invalidate stats so user sees progress.
  if (state.worker?.running) {
    if (state.workerPollHandle) clearTimeout(state.workerPollHandle);
    state.workerPollHandle = setTimeout(async () => {
      state.stats = null;
      state.workerPollHandle = null;
      await refreshWorkerStatus();
    }, 2000);
  } else if (state.workerPollHandle) {
    clearTimeout(state.workerPollHandle);
    state.workerPollHandle = null;
  }
  paint();
}

async function startWorker() {
  const f = state.workerForm;
  f.busy = true;
  paint();
  try {
    await postJson("/api/research/worker/start", {
      kind: f.kind,
      max_jobs: Number(f.max_jobs),
      lease_seconds: Number(f.lease_seconds),
      hermes_timeout_seconds: Number(f.hermes_timeout_seconds),
    });
    showToast(f.kind === "loop" ? "Worker 已持续启动" : "Worker 已启动（跑一次）");
    state.stats = null;
    state.runs = null;
    state.requests = null;
    await refreshWorkerStatus();
  } catch (err) {
    showToast(err.message, true);
  } finally {
    f.busy = false;
    paint();
  }
}

async function stopWorker() {
  state.workerForm.busy = true;
  paint();
  try {
    await postJson("/api/research/worker/stop", {});
    showToast("Worker 已停止");
    await refreshWorkerStatus();
  } catch (err) {
    showToast(err.message, true);
  } finally {
    state.workerForm.busy = false;
    paint();
  }
}

// ---------------------------------------------------------------------------
// Tab 5. 请求列表 (+ 详情)
// ---------------------------------------------------------------------------

function buildRequestsUrl() {
  const p = new URLSearchParams();
  if (state.reqFilter.category) p.set("category", state.reqFilter.category);
  if (state.reqFilter.status) p.set("status", state.reqFilter.status);
  return p.toString() ? `/api/research/requests?${p}` : "/api/research/requests";
}

function renderRequestsTab() {
  ensureLoaded("requests", buildRequestsUrl());
  ensureLoaded("categories", "/api/research/categories"); // for filter options
  const flag = state.flags.requests || {};
  if (flag.loading && !state.requests) return loadingPlaceholder("请求列表");
  if (flag.error) return errorPlaceholder(flag.error, "requests");
  const items = state.requests || [];
  const cats = state.categories || [];
  return `
    <section class="card">
      <div class="card-header">
        <div>
          <div class="card-title">生成请求</div>
          <div class="card-subtitle">点击行查看详情（含所有 run / sources / findings）</div>
        </div>
        <span class="pill">${items.length} 项</span>
      </div>
      <div class="border-b border-line px-5 py-3 flex flex-wrap items-center gap-2">
        <label class="flex items-center gap-1.5 text-[12px] text-ink-500">类别
          <select id="req-filter-cat" class="h-8 rounded-md border border-line-strong bg-surface px-2 text-[12px] outline-none">
            <option value=""${state.reqFilter.category === "" ? " selected" : ""}>全部</option>
            ${cats.map(c => `<option value="${escapeHtml(c.code)}"${state.reqFilter.category === c.code ? " selected" : ""}>${escapeHtml(c.code)}</option>`).join("")}
          </select>
        </label>
        <label class="flex items-center gap-1.5 text-[12px] text-ink-500">状态
          <select id="req-filter-status" class="h-8 rounded-md border border-line-strong bg-surface px-2 text-[12px] outline-none">
            <option value=""${state.reqFilter.status === "" ? " selected" : ""}>全部</option>
            ${REQUEST_STATUSES.map(s => `<option value="${escapeHtml(s)}"${state.reqFilter.status === s ? " selected" : ""}>${escapeHtml(s)}</option>`).join("")}
          </select>
        </label>
        <button id="req-clear-filter" class="ml-auto text-[12px] text-ink-500 hover:text-ink-900">清空</button>
      </div>
      ${items.length ? renderRequestsRows(items) : `<div class="empty m-5">没有匹配的请求</div>`}
    </section>
  `;
}

function renderRequestsRows(items) {
  return `
    <div class="overflow-x-auto">
      <table class="w-full min-w-[760px] text-left">
        <thead class="border-b border-line text-[11px] font-medium text-ink-500">
          <tr>
            <th class="px-5 py-3 font-medium">ID</th>
            <th class="px-5 py-3 font-medium">类别</th>
            <th class="px-5 py-3 font-medium">话题</th>
            <th class="px-5 py-3 font-medium">目标</th>
            <th class="px-5 py-3 font-medium">状态</th>
            <th class="px-5 py-3 font-medium">创建时间</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-line text-[13px]">
          ${items.map(r => `
            <tr class="hover:bg-ink-50 cursor-pointer research-request-row" data-id="${escapeHtml(r.id)}">
              <td class="px-5 py-3 font-mono text-[11px] text-ink-500">${escapeHtml(r.id.slice(0, 8))}…</td>
              <td class="px-5 py-3">${softPill(categoryLabel(r.category), categoryTone(r.category))}</td>
              <td class="px-5 py-3"><div class="max-w-[280px] truncate">${escapeHtml(r.topic)}</div></td>
              <td class="px-5 py-3 tabular-nums">${r.target_count}</td>
              <td class="px-5 py-3">${statusIndicator(r.status)}</td>
              <td class="px-5 py-3 text-[11px] text-ink-500 tabular-nums">${escapeHtml(r.created_at?.slice(0, 19) ?? "-")}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Detail view (within Tab 5)
// ---------------------------------------------------------------------------

function renderDetailTab() {
  if (state.detail === null) {
    fetchDetail(state.detailId);
    return loadingPlaceholder("详情");
  }
  const { request, latest_run, runs = [], sources = [], findings_by_kind = {} } = state.detail;
  return `
    <button class="research-back text-[13px] text-brand-600 mb-4 inline-flex items-center gap-1">
      <i data-lucide="arrow-left" class="size-4"></i> 返回列表
    </button>

    <section class="card p-5">
      <div class="flex items-center gap-2">
        ${softPill(categoryLabel(request.category), categoryTone(request.category))}
        ${statusIndicator(request.status)}
      </div>
      <h2 class="mt-2 text-[15px] font-semibold">${escapeHtml(request.topic)}</h2>
      <div class="mt-1 font-mono text-[11px] text-ink-500">${escapeHtml(request.id)}</div>
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
      <div class="card-header">
        <div><div class="card-title">所有运行</div><div class="card-subtitle">按 attempt 升序；最新一次会带 sources / findings</div></div>
        <span class="pill">${runs.length}</span>
      </div>
      ${runs.length ? renderDetailRunsTable(runs, latest_run) : `<div class="empty m-5">尚无运行</div>`}
    </section>

    ${(sources.length || Object.keys(findings_by_kind).length) ? `
      <div class="grid gap-5 xl:grid-cols-2 mt-5">
        <section class="card">
          <div class="card-header"><div><div class="card-title">Sources</div><div class="card-subtitle">最新一次 run 采集的引用</div></div><span class="pill">${sources.length}</span></div>
          ${sources.length ? renderDetailSources(sources) : `<div class="empty m-5">无 sources</div>`}
        </section>
        <section class="card">
          <div class="card-header"><div><div class="card-title">Findings</div><div class="card-subtitle">按 kind 分组</div></div></div>
          ${Object.keys(findings_by_kind).length ? renderDetailFindings(findings_by_kind) : `<div class="empty m-5">无 findings</div>`}
        </section>
      </div>
    ` : ""}
  `;
}

function renderDetailRunsTable(runs, latest) {
  return `
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
            <tr class="${latest && run.id === latest.id ? "bg-brand-50/40" : ""}">
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
  `;
}

function renderDetailSources(sources) {
  return `
    <div class="divide-y divide-line">
      ${sources.map(s => `
        <div class="px-5 py-3.5">
          <a href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer" class="text-[13px] font-medium text-brand-600 hover:text-brand-700 break-all">${escapeHtml(s.title || s.url)}</a>
          <p class="mt-1 text-[12px] text-ink-600">${escapeHtml(s.summary || "")}</p>
          ${s.raw_text_path ? `<div class="mt-1.5 text-[11px] text-ink-500 font-mono">${escapeHtml(s.raw_text_path)}</div>` : ""}
        </div>
      `).join("")}
    </div>
  `;
}

function renderDetailFindings(byKind) {
  return `
    <div class="divide-y divide-line">
      ${Object.entries(byKind).map(([kind, items]) => `
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
  `;
}

async function fetchDetail(id) {
  if (state.detail !== null) return;
  if (state.flags.detail?.loading) return;
  setFlag("detail", { loading: true });
  try {
    state.detail = await api(`/api/research/requests/${id}`);
    setFlag("detail", { loading: false });
    paint();
  } catch (err) {
    showToast(err.message, true);
    setFlag("detail", { loading: false });
    state.detailId = null;
    state.detail = null;
    paint();
  }
}

// ---------------------------------------------------------------------------
// Tab 6. 运行记录
// ---------------------------------------------------------------------------

function buildRunsUrl() {
  const p = new URLSearchParams();
  if (state.runFilter.status) p.set("status", state.runFilter.status);
  if (state.runFilter.claimed_by) p.set("claimed_by", state.runFilter.claimed_by);
  return p.toString() ? `/api/research/runs?${p}` : "/api/research/runs";
}

function renderRunsTab() {
  ensureLoaded("runs", buildRunsUrl());
  const flag = state.flags.runs || {};
  if (flag.loading && !state.runs) return loadingPlaceholder("运行记录");
  if (flag.error) return errorPlaceholder(flag.error, "runs");
  const items = state.runs || [];
  return `
    <section class="card">
      <div class="card-header">
        <div>
          <div class="card-title">运行记录</div>
          <div class="card-subtitle">research_runs 全表；按 status / claimed_by 过滤</div>
        </div>
        <span class="pill">${items.length} 项</span>
      </div>
      <div class="border-b border-line px-5 py-3 flex flex-wrap items-center gap-2">
        <label class="flex items-center gap-1.5 text-[12px] text-ink-500">状态
          <select id="run-filter-status" class="h-8 rounded-md border border-line-strong bg-surface px-2 text-[12px] outline-none">
            <option value=""${state.runFilter.status === "" ? " selected" : ""}>全部</option>
            ${RUN_STATUSES.map(s => `<option value="${escapeHtml(s)}"${state.runFilter.status === s ? " selected" : ""}>${escapeHtml(s)}</option>`).join("")}
          </select>
        </label>
        <label class="flex items-center gap-1.5 text-[12px] text-ink-500">claimed_by
          <input id="run-filter-claimed-by" value="${escapeHtml(state.runFilter.claimed_by)}"
            class="h-8 w-44 rounded-md border border-line-strong bg-surface px-2 text-[12px] outline-none" placeholder="worker id" />
        </label>
        <button id="run-clear-filter" class="ml-auto text-[12px] text-ink-500 hover:text-ink-900">清空</button>
      </div>
      ${items.length ? renderRunsRows(items) : `<div class="empty m-5">没有匹配的运行</div>`}
    </section>
  `;
}

function renderRunsRows(items) {
  return `
    <div class="overflow-x-auto">
      <table class="w-full min-w-[860px] text-left">
        <thead class="border-b border-line text-[11px] font-medium text-ink-500">
          <tr>
            <th class="px-5 py-3 font-medium">Run ID</th>
            <th class="px-5 py-3 font-medium">类别</th>
            <th class="px-5 py-3 font-medium">尝试</th>
            <th class="px-5 py-3 font-medium">状态</th>
            <th class="px-5 py-3 font-medium">Worker</th>
            <th class="px-5 py-3 font-medium">profile</th>
            <th class="px-5 py-3 font-medium">started_at</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-line text-[13px]">
          ${items.map(r => `
            <tr class="hover:bg-ink-50">
              <td class="px-5 py-3 font-mono text-[11px] text-ink-500">${escapeHtml(r.id.slice(0, 8))}…</td>
              <td class="px-5 py-3">${r.category ? softPill(categoryLabel(r.category), categoryTone(r.category)) : "-"}</td>
              <td class="px-5 py-3 tabular-nums">${r.attempt}</td>
              <td class="px-5 py-3">${statusIndicator(r.status)}</td>
              <td class="px-5 py-3 font-mono text-[12px]">${escapeHtml(r.claimed_by || "-")}</td>
              <td class="px-5 py-3 font-mono text-[12px]">${escapeHtml(r.profile_name_used || "-")}</td>
              <td class="px-5 py-3 text-[11px] text-ink-500 tabular-nums">${escapeHtml(r.started_at?.slice(0, 19) || "-")}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// helpers / small fragments
// ---------------------------------------------------------------------------

function kv(key, value) {
  return `
    <div>
      <dt class="text-[10px] uppercase tracking-wider text-ink-400 mb-0.5">${escapeHtml(key)}</dt>
      <dd class="text-[12px] text-ink-700">${value}</dd>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// public hooks
// ---------------------------------------------------------------------------

export function render() {
  paint();
}

export function bind() {
  document.addEventListener("click", (e) => {
    const node = root();
    if (!node || !node.contains(e.target)) return;

    const tab = e.target.closest(".research-tab");
    if (tab) {
      state.activeTab = tab.dataset.tab;
      state.detailId = null;
      state.detail = null;
      paint();
      return;
    }
    if (e.target.closest(".research-back")) {
      state.detailId = null;
      state.detail = null;
      paint();
      return;
    }
    const retry = e.target.closest(".research-retry");
    if (retry) {
      const key = retry.dataset.key;
      const url = {
        categories: "/api/research/categories",
        bindings: "/api/profile/bindings",
        stats: "/api/research/queue/stats",
        requests: buildRequestsUrl(),
        runs: buildRunsUrl(),
      }[key];
      if (url) forceReload(key, url);
      return;
    }
    const row = e.target.closest(".research-request-row");
    if (row) {
      state.detailId = row.dataset.id;
      state.detail = null;
      paint();
      return;
    }
    if (e.target.closest("#stats-refresh")) {
      forceReload("stats", "/api/research/queue/stats");
      refreshWorkerStatus();
      return;
    }
    if (e.target.closest("#worker-start")) {
      startWorker();
      return;
    }
    if (e.target.closest("#worker-stop")) {
      stopWorker();
      return;
    }
    if (e.target.closest("#req-clear-filter")) {
      state.reqFilter = { category: "", status: "" };
      forceReload("requests", buildRequestsUrl());
      return;
    }
    if (e.target.closest("#run-clear-filter")) {
      state.runFilter = { status: "", claimed_by: "" };
      forceReload("runs", buildRunsUrl());
      return;
    }
    if (e.target.closest("#form-reset")) {
      resetForm();
      return;
    }
  });

  document.addEventListener("submit", (e) => {
    if (e.target?.id === "research-submit-form") {
      e.preventDefault();
      handleSubmit();
    }
  });

  document.addEventListener("change", (e) => {
    const node = root();
    if (!node || !node.contains(e.target)) return;

    if (e.target.id === "req-filter-cat") {
      state.reqFilter.category = e.target.value;
      forceReload("requests", buildRequestsUrl());
    } else if (e.target.id === "req-filter-status") {
      state.reqFilter.status = e.target.value;
      forceReload("requests", buildRequestsUrl());
    } else if (e.target.id === "run-filter-status") {
      state.runFilter.status = e.target.value;
      forceReload("runs", buildRunsUrl());
    } else if (e.target.id === "form-category") {
      state.form.category = e.target.value;
    } else if (e.target.id === "worker-kind") {
      state.workerForm.kind = e.target.value;
      paint();
    } else if (e.target.dataset?.difficulty) {
      state.form.distribution[e.target.dataset.difficulty] = Number(e.target.value) || 0;
      paint();
    }
  });

  let runFilterTimer;
  document.addEventListener("input", (e) => {
    if (e.target.id === "run-filter-claimed-by") {
      clearTimeout(runFilterTimer);
      const value = e.target.value;
      runFilterTimer = setTimeout(() => {
        state.runFilter.claimed_by = value;
        forceReload("runs", buildRunsUrl());
      }, 300);
      return;
    }
    if (e.target.id === "form-topic") state.form.topic = e.target.value;
    else if (e.target.id === "form-target-count") state.form.target_count = e.target.value;
    else if (e.target.id === "form-max-attempts") state.form.max_attempts = e.target.value;
    else if (e.target.id === "form-seed-urls") state.form.seed_urls = e.target.value;
    else if (e.target.id === "worker-max-jobs") state.workerForm.max_jobs = e.target.value;
    else if (e.target.id === "worker-lease") state.workerForm.lease_seconds = e.target.value;
    else if (e.target.id === "worker-hermes-timeout") state.workerForm.hermes_timeout_seconds = e.target.value;
  });
}
