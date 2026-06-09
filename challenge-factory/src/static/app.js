const appState = {
  data: null,
  view: "overview",
  category: "all",
  search: "",
  timer: null,
};

const labels = {
  overview: "生产概览",
  progress: "实时进度",
  challenges: "题目",
  shards: "任务分片",
  logs: "运行日志",
};

const categoryMeta = {
  web: { label: "Web", soft: "bg-cyan-50 text-cyan-800" },
  pwn: { label: "Pwn", soft: "bg-rose-50 text-rose-800" },
  re: { label: "Reverse", soft: "bg-amber-50 text-amber-800" },
};

const stageLabels = {
  queued: "排队",
  design: "设计",
  implement: "实现",
  build: "构建",
  validate: "验证",
  document: "文档",
  complete: "完成",
};

function statusClass(status) {
  if (status === "passed" || status === "done") return "bg-emerald-50 text-emerald-700";
  if (status === "failed") return "bg-rose-50 text-rose-700";
  if (status === "running") return "bg-amber-50 text-amber-700";
  return "bg-neutral-100 text-neutral-600";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function showToast(message, error = false) {
  const toast = document.querySelector("#toast");
  toast.textContent = message;
  toast.className = `fixed bottom-5 right-5 z-50 border px-4 py-3 text-sm shadow-lg ${
    error ? "border-rose-300 bg-rose-50 text-rose-800" : "border-line bg-white text-ink"
  }`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.add("hidden"), 2400);
  toast.classList.remove("hidden");
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || payload.error || "请求失败");
  return payload;
}

async function loadState() {
  document.querySelector("#refreshIcon")?.classList.add("animate-spin");
  try {
    appState.data = await api("/api/state");
    render();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    document.querySelector("#refreshIcon")?.classList.remove("animate-spin");
    scheduleRefresh();
  }
}

function scheduleRefresh() {
  clearTimeout(appState.timer);
  const active = appState.data?.process?.running
    || appState.data?.progress?.snapshots?.some((item) => item.status === "running");
  appState.timer = setTimeout(loadState, active ? 2000 : 8000);
}

function setView(view) {
  appState.view = view;
  document.querySelectorAll("[data-view]").forEach((node) => {
    node.classList.toggle("active", node.dataset.view === view);
  });
  document.querySelectorAll(".nav-item").forEach((button) => {
    const active = button.dataset.target === view;
    button.classList.toggle("bg-neutral-100", active);
    button.classList.toggle("text-ink", active);
    button.classList.toggle("text-neutral-500", !active);
  });
  document.querySelector("#pageTitle").textContent = labels[view];
  if (window.innerWidth < 1024) document.querySelector("#sidebarNav").classList.add("hidden");
}

function renderMetrics() {
  const summary = appState.data.summary;
  const active = summary.queue.pending + summary.queue.running;
  const metrics = [
    ["题目总数", summary.challenges, `Web ${summary.categories.web} · Pwn ${summary.categories.pwn} · Re ${summary.categories.re}`, "flag"],
    ["EXP 已通过", summary.validated, `${summary.challenges ? Math.round(summary.validated / summary.challenges * 100) : 0}% 验证率`, "shield-check"],
    ["构建已通过", summary.built, `${Math.max(0, summary.challenges - summary.built)} 个待处理`, "package-check"],
    ["活动队列", active, `${summary.queue.failed} 失败 · ${summary.queue.done} 完成`, "layers-3"],
  ];
  document.querySelector("#metricGrid").innerHTML = metrics.map(([label, value, note, icon]) => `
    <article class="border border-line bg-white p-5 shadow-panel">
      <div class="flex items-center justify-between text-xs font-medium text-neutral-500">
        <span>${label}</span><i data-lucide="${icon}" class="size-4"></i>
      </div>
      <div class="mt-5 text-3xl font-semibold">${value}</div>
      <div class="mt-2 truncate text-xs text-neutral-500">${note}</div>
    </article>
  `).join("");
}

function renderRecent() {
  const rows = appState.data.challenges.slice(-6).reverse();
  document.querySelector("#recentChallenges").innerHTML = rows.length ? rows.map((item) => `
    <div class="flex items-center gap-3 px-5 py-3.5">
      <div class="grid size-8 shrink-0 place-items-center ${categoryMeta[item.category]?.soft || "bg-neutral-100"} text-xs font-semibold">
        ${(categoryMeta[item.category]?.label || item.category).slice(0, 2)}
      </div>
      <div class="min-w-0 flex-1">
        <div class="truncate text-sm font-medium">${escapeHtml(item.title)}</div>
        <div class="mt-0.5 truncate text-xs text-neutral-500">${escapeHtml(item.id)} · ${escapeHtml(item.runtime)} / ${escapeHtml(item.framework)}</div>
      </div>
      <span class="px-2 py-1 text-xs font-medium ${statusClass(item.solve_status)}">${escapeHtml(item.solve_status)}</span>
    </div>
  `).join("") : `<div class="px-5 py-12 text-center text-sm text-neutral-500">暂无题目</div>`;
}

function renderProgress() {
  const snapshots = appState.data.progress.snapshots.filter((item) => item.challenge_id);
  const events = appState.data.progress.events;
  const warning = document.querySelector("#storageWarning");
  warning.textContent = appState.data.progress.storage?.warning || "";
  warning.classList.toggle("hidden", !warning.textContent);
  document.querySelector("#progressList").innerHTML = snapshots.length ? snapshots.map((item) => `
    <article class="border-b border-line px-5 py-4 last:border-b-0">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <div class="flex items-center gap-2">
            <span class="font-mono text-sm font-semibold">${escapeHtml(item.challenge_id)}</span>
            <span class="px-2 py-1 text-xs font-medium ${statusClass(item.status)}">${escapeHtml(item.status)}</span>
          </div>
          <div class="mt-1 truncate text-xs text-neutral-500">${escapeHtml(item.shard)} · ${escapeHtml(item.worker || "未分配")}</div>
        </div>
        <div class="text-right">
          <div class="text-sm font-semibold">${item.percent}%</div>
          <div class="mt-1 text-xs text-neutral-500">${stageLabels[item.stage] || item.stage}</div>
        </div>
      </div>
      <div class="mt-3 h-2 overflow-hidden bg-neutral-100">
        <div class="h-full transition-all ${item.status === "failed" ? "bg-rose-500" : "bg-emerald-600"}" style="width:${item.percent}%"></div>
      </div>
      <div class="mt-2 flex items-center justify-between gap-4 text-xs text-neutral-500">
        <span class="truncate">${escapeHtml(item.message || "等待进度更新")}</span>
        <span class="shrink-0">${escapeHtml(item.updated_at)}</span>
      </div>
    </article>
  `).join("") : `<div class="px-5 py-16 text-center text-sm text-neutral-500">启动 Worker 后，这里会显示逐题阶段进度</div>`;

  document.querySelector("#eventList").innerHTML = events.length ? events.map((item) => `
    <div class="relative border-l border-line pb-5 pl-5 last:pb-0">
      <span class="absolute -left-1.5 top-1 size-3 border-2 border-white ${item.status === "failed" ? "bg-rose-500" : item.status === "running" ? "bg-amber-500" : "bg-emerald-600"}"></span>
      <div class="flex items-center justify-between gap-3">
        <span class="truncate text-sm font-medium">${escapeHtml(item.challenge_id || item.shard)}</span>
        <span class="shrink-0 text-xs text-neutral-400">${escapeHtml(item.created_at)}</span>
      </div>
      <div class="mt-1 text-xs text-neutral-500">${stageLabels[item.stage] || item.stage} · ${escapeHtml(item.status)}</div>
      ${item.message ? `<p class="mt-1 text-xs leading-5 text-neutral-600">${escapeHtml(item.message)}</p>` : ""}
    </div>
  `).join("") : `<div class="py-10 text-center text-sm text-neutral-500">暂无事件</div>`;
}

function renderChallengeTable() {
  const query = appState.search.toLowerCase();
  const rows = appState.data.challenges.filter((item) => {
    const categoryMatch = appState.category === "all" || item.category === appState.category;
    return categoryMatch && (!query || `${item.id} ${item.title} ${item.runtime} ${item.framework}`.toLowerCase().includes(query));
  });
  document.querySelector("#challengeTable").innerHTML = rows.length ? rows.map((item) => `
    <tr class="hover:bg-neutral-50">
      <td class="px-4 py-3"><div class="font-medium">${escapeHtml(item.title)}</div><div class="mt-0.5 text-xs text-neutral-500">${escapeHtml(item.id)}</div></td>
      <td class="px-4 py-3"><span class="px-2 py-1 text-xs font-medium ${categoryMeta[item.category]?.soft || "bg-neutral-100"}">${categoryMeta[item.category]?.label || item.category}</span></td>
      <td class="px-4 py-3 capitalize text-neutral-600">${escapeHtml(item.difficulty)}</td>
      <td class="px-4 py-3 text-neutral-600">${escapeHtml(item.runtime)} · ${escapeHtml(item.framework)}</td>
      <td class="px-4 py-3"><span class="px-2 py-1 text-xs ${statusClass(item.build_status)}">${escapeHtml(item.build_status)}</span></td>
      <td class="px-4 py-3"><span class="px-2 py-1 text-xs ${statusClass(item.solve_status)}">${escapeHtml(item.solve_status)}</span></td>
      <td class="px-4 py-3 text-neutral-500">${escapeHtml(item.updated)}</td>
    </tr>
  `).join("") : `<tr><td colspan="7" class="px-4 py-16 text-center text-sm text-neutral-500">没有匹配的题目</td></tr>`;
}

function renderShards() {
  const states = [["pending", "待处理"], ["running", "运行中"], ["failed", "失败"], ["done", "已完成"]];
  document.querySelector("#shardColumns").innerHTML = states.map(([key, label]) => {
    const rows = appState.data.shards.filter((item) => item.state === key);
    return `<section>
      <div class="mb-3 flex h-9 items-center justify-between text-sm font-semibold"><span>${label}</span><span class="grid size-6 place-items-center bg-neutral-200 text-xs">${rows.length}</span></div>
      <div class="space-y-2">${rows.length ? rows.map((item) => `
        <article class="border border-line bg-white p-4 shadow-panel">
          <div class="flex items-start justify-between gap-2">
            <div class="min-w-0"><div class="truncate text-sm font-medium">${escapeHtml(item.name)}</div><div class="mt-1 text-xs text-neutral-500">${item.count} 题 · ${escapeHtml(item.categories.join(", ") || "-")}</div></div>
            ${(key === "failed" || (key === "running" && !appState.data.process.running)) ? `<button class="requeue-shard grid size-8 shrink-0 place-items-center border border-line hover:bg-neutral-50" data-state="${key}" data-name="${escapeHtml(item.name)}" title="重新入队"><i data-lucide="rotate-ccw" class="size-4"></i></button>` : ""}
          </div>
          <div class="mt-4 flex items-center justify-between text-xs text-neutral-500"><span class="px-2 py-1 ${statusClass(key)}">${key}</span><span>${escapeHtml(item.updated)}</span></div>
        </article>`).join("") : `<div class="border border-dashed border-line py-10 text-center text-xs text-neutral-400">空</div>`}
      </div>
    </section>`;
  }).join("");
}

function renderLogs() {
  document.querySelector("#logList").innerHTML = appState.data.logs.length ? appState.data.logs.map((log) => `
    <button class="log-button block w-full px-4 py-3 text-left hover:bg-neutral-50" data-name="${escapeHtml(log.name)}">
      <div class="truncate text-sm font-medium">${escapeHtml(log.name)}</div>
      <div class="mt-1 flex justify-between text-xs text-neutral-500"><span>${Math.ceil(log.size / 1024)} KB</span><span>${escapeHtml(log.updated)}</span></div>
    </button>
  `).join("") : `<div class="px-4 py-12 text-center text-sm text-neutral-500">暂无日志</div>`;
}

function renderProcess() {
  const process = appState.data.process;
  document.querySelector("#workerLabel").textContent = process.message || "空闲";
  document.querySelector("#workerBar").className = `h-full bg-emerald-600 transition-all ${process.running ? "w-full animate-pulse" : "w-0"}`;
  ["workerButton", "validateButton", "mobileWorkerButton", "mobileValidateButton"].forEach((id) => {
    const button = document.querySelector(`#${id}`);
    button.disabled = process.running;
    button.classList.toggle("opacity-50", process.running);
  });
}

function render() {
  document.querySelector("#updatedAt").textContent = `最后同步 ${appState.data.updated_at}`;
  renderMetrics();
  renderRecent();
  renderProgress();
  renderChallengeTable();
  renderShards();
  renderLogs();
  renderProcess();
  setView(appState.view);
  lucide.createIcons();
  bindDynamicEvents();
}

function bindDynamicEvents() {
  document.querySelectorAll(".requeue-shard").forEach((button) => button.addEventListener("click", async () => {
    try {
      const result = await api(`/api/shards/${button.dataset.state}/${encodeURIComponent(button.dataset.name)}/requeue`, { method: "POST" });
      showToast(result.message);
      await loadState();
    } catch (error) {
      showToast(error.message, true);
    }
  }));
  document.querySelectorAll(".log-button").forEach((button) => button.addEventListener("click", () => openLog(button.dataset.name)));
}

async function openLog(name) {
  try {
    const result = await api(`/api/logs/${encodeURIComponent(name)}`);
    document.querySelector("#logTitle").textContent = result.name;
    document.querySelector("#logContent").textContent = result.content || "日志为空";
  } catch (error) {
    showToast(error.message, true);
  }
}

async function runAction(kind) {
  try {
    const result = await api(`/api/actions/${kind}`, { method: "POST" });
    showToast(result.message);
    if (kind === "worker") setView("progress");
    await loadState();
  } catch (error) {
    showToast(error.message, true);
  }
}

document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => setView(button.dataset.target)));
document.querySelectorAll("[data-jump]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.jump)));
document.querySelector("#mobileMenu").addEventListener("click", () => document.querySelector("#sidebarNav").classList.toggle("hidden"));
document.querySelector("#refreshButton").addEventListener("click", loadState);
document.querySelector("#workerButton").addEventListener("click", () => runAction("worker"));
document.querySelector("#validateButton").addEventListener("click", () => runAction("validate"));
document.querySelector("#mobileWorkerButton").addEventListener("click", () => runAction("worker"));
document.querySelector("#mobileValidateButton").addEventListener("click", () => runAction("validate"));
document.querySelector("#challengeSearch").addEventListener("input", (event) => {
  appState.search = event.target.value;
  renderChallengeTable();
});
document.querySelectorAll(".filter-button").forEach((button) => button.addEventListener("click", () => {
  appState.category = button.dataset.category;
  document.querySelectorAll(".filter-button").forEach((item) => {
    item.className = `filter-button h-9 px-3 text-sm font-medium ${item === button ? "bg-ink text-white" : "border border-line bg-white"}`;
  });
  renderChallengeTable();
}));
document.querySelector("#copyLog").addEventListener("click", async () => {
  await navigator.clipboard.writeText(document.querySelector("#logContent").textContent);
  showToast("日志已复制");
});

loadState();
