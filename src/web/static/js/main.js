import { appState, scheduleRefresh } from "./state.js";
import { api } from "./api.js";
import { showToast } from "./ui/toast.js";
import { initIcons } from "./ui/icons.js";
import { registerViews, setView, jumpTo } from "./router.js";

import * as overview from "./views/overview.js";
import * as workerPool from "./views/worker-pool.js";
import * as challenges from "./views/challenges.js";
import * as shards from "./views/shards.js";
import * as logs from "./views/logs.js";
import * as researchSubmit from "./views/research-submit.js";
import * as researchRequests from "./views/research-requests.js";
import * as researchRuns from "./views/research-runs.js";
import * as researchLogs from "./views/research-logs.js";
import * as designTasks from "./views/design-tasks.js";
import * as buildAttempts from "./views/build-attempts.js";

const views = {
  overview,
  "worker-pool": workerPool,
  challenges,
  shards,
  logs,
  "research-submit": researchSubmit,
  "research-requests": researchRequests,
  "research-runs": researchRuns,
  "research-logs": researchLogs,
  "design-tasks": designTasks,
  "build-attempts": buildAttempts,
};

registerViews({
  overview: overview.render,
  "worker-pool": workerPool.render,
  challenges: challenges.render,
  shards: shards.render,
  logs: logs.render,
  "research-submit": researchSubmit.render,
  "research-requests": researchRequests.render,
  "research-runs": researchRuns.render,
  "research-logs": researchLogs.render,
  "design-tasks": designTasks.render,
  "build-attempts": buildAttempts.render,
});

function routeFromHash() {
  const raw = window.location.hash || "";
  if (!raw) return false;
  if (!raw.startsWith("#/build-attempts")) {
    clearLocationHash();
    setView("overview");
    return false;
  }

  const hash = raw.slice(2);
  const [path, query = ""] = hash.split("?");
  const [, detailId = null] = path.split("/");
  const params = new URLSearchParams(query);
  const filters = {};
  for (const key of ["status", "worker", "category", "design_task_id", "generation_request_id"]) {
    if (params.has(key)) filters[key] = params.get(key) || "";
  }

  buildAttempts.openBuildAttemptsRoute({
    detailId: detailId ? decodeURIComponent(detailId) : null,
    filters,
  });
  setView("build-attempts");
  return true;
}

function clearLocationHash() {
  if (!window.location.hash) return;
  history.replaceState(null, "", window.location.pathname + window.location.search);
}

function navigateTo(target) {
  if (!target) return;
  if (target === "build-attempts") {
    if (window.location.hash === "#/build-attempts") routeFromHash();
    else window.location.hash = "#/build-attempts";
    return;
  }
  clearLocationHash();
  setView(target);
}

async function loadState() {
  if (appState.stateLoading) return;
  appState.stateLoading = true;
  try {
    appState.data = appState.view === "build-attempts"
      ? await api("/api/ui-state")
      : await api("/api/state");
    renderAll();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    appState.stateLoading = false;
    scheduleRefresh(loadState);
  }
}

function renderAll() {
  const activeView = views[appState.view];
  activeView?.render?.(appState.data);
  renderProcess();
  setView(appState.view);
  initIcons();
}

function renderProcess() {
  const proc = appState.data.process || {};
  const dot = document.querySelector("#sidebarWorkerDot");
  const count = document.querySelector("#sidebarWorkerCount");
  const entry = document.querySelector("#sidebarWorkerPoolEntry");

  if (proc.running) {
    if (dot) dot.className = "sidebar-worker-dot running";
    if (count) count.textContent = "运行中";
    if (entry) entry.classList.remove("hidden");
  } else {
    if (dot) dot.className = "sidebar-worker-dot idle";
    if (count) count.textContent = "空闲";
    if (entry) entry.classList.remove("hidden");
  }
}

document.addEventListener("click", (event) => {
  const nav = event.target.closest(".sidebar-nav-item, .sidebar-worker-pool-entry");
  if (nav) {
    const target = nav.dataset.target;
    if (!target) return;
    // 移动端：选择导航项后收起菜单
    if (window.innerWidth < 1024) {
      document.querySelector("#sidebarNav")?.classList.add("hidden");
    }
    navigateTo(target);
    return;
  }
  const jump = event.target.closest("[data-jump]");
  if (jump) {
    const target = jump.dataset.jump;
    if (target === "build-attempts") navigateTo(target);
    else {
      clearLocationHash();
      jumpTo(target);
    }
  }
});

window.addEventListener("hashchange", routeFromHash);
document.addEventListener("ctf:open-design-task", (event) => {
  const taskId = event.detail?.taskId;
  if (taskId) designTasks.showDesignTaskDetail(taskId);
});

document.querySelector("#mobileMenu")?.addEventListener("click", () => {
  document.querySelector("#sidebarNav")?.classList.toggle("hidden");
});
challenges.bind?.(loadState);
shards.bind?.(loadState);
logs.bind?.(loadState);
researchSubmit.bind?.(loadState);
researchRequests.bind?.(loadState);
researchRuns.bind?.(loadState);
researchLogs.bind?.(loadState);
designTasks.bind?.(loadState);
buildAttempts.bind?.(loadState);
workerPool.bind?.(loadState);

routeFromHash();
loadState();
