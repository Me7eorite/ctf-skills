import { appState, scheduleRefresh } from "./state.js";
import { api } from "./api.js";
import { showToast } from "./ui/toast.js";
import { initIcons } from "./ui/icons.js";
import { registerViews, setView, jumpTo } from "./router.js";

import * as overview from "./views/overview.js";
import * as progress from "./views/progress.js";
import * as challenges from "./views/challenges.js";
import * as shards from "./views/shards.js";
import * as logs from "./views/logs.js";
import * as seeds from "./views/seeds.js";
import * as researchSubmit from "./views/research-submit.js";
import * as researchRequests from "./views/research-requests.js";
import * as researchRuns from "./views/research-runs.js";
import * as researchLogs from "./views/research-logs.js";
import * as designTasks from "./views/design-tasks.js";
import * as buildAttempts from "./views/build-attempts.js";

const views = {
  overview,
  progress,
  challenges,
  shards,
  logs,
  seeds,
  "research-submit": researchSubmit,
  "research-requests": researchRequests,
  "research-runs": researchRuns,
  "research-logs": researchLogs,
  "design-tasks": designTasks,
  "build-attempts": buildAttempts,
};

registerViews({
  overview: overview.render,
  progress: progress.render,
  challenges: challenges.render,
  shards: shards.render,
  logs: logs.render,
  seeds: seeds.render,
  "research-submit": researchSubmit.render,
  "research-requests": researchRequests.render,
  "research-runs": researchRuns.render,
  "research-logs": researchLogs.render,
  "design-tasks": designTasks.render,
  "build-attempts": buildAttempts.render,
});

function routeFromHash() {
  const raw = window.location.hash || "";
  if (!raw.startsWith("#/build-attempts")) return false;

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

async function loadState() {
  try {
    appState.data = await api("/api/state");
    renderAll();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    scheduleRefresh(loadState);
  }
}

function renderAll() {
  for (const view of Object.values(views)) view.render?.(appState.data);
  renderProcess();
  setView(appState.view);
  initIcons();
}

function renderProcess() {
  const proc = appState.data.process || {};
  const label = document.querySelector("#workerLabel");
  const bar = document.querySelector("#workerBar");
  const dot = document.querySelector("#workerDot");

  if (proc.running) {
    if (label) { label.textContent = proc.message || "运行中"; label.className = "sidebar-worker-state running"; }
    if (bar) { bar.className = "sidebar-worker-bar-fill running"; }
    if (dot) { dot.className = "sidebar-worker-dot running"; }
  } else {
    if (label) { label.textContent = "空闲"; label.className = "sidebar-worker-state idle"; }
    if (bar) { bar.className = "sidebar-worker-bar-fill idle"; }
    if (dot) { dot.className = "sidebar-worker-dot idle"; }
  }
}

document.addEventListener("click", (event) => {
  const nav = event.target.closest(".sidebar-nav-item");
  if (nav) {
    if (nav.dataset.target === "build-attempts") {
      if (window.location.hash === "#/build-attempts") routeFromHash();
      else window.location.hash = "#/build-attempts";
      return;
    }
    setView(nav.dataset.target);
    return;
  }
  const jump = event.target.closest("[data-jump]");
  if (jump) jumpTo(jump.dataset.jump);
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
seeds.bind?.(loadState);
shards.bind?.(loadState);
logs.bind?.(loadState);
researchSubmit.bind?.(loadState);
researchRequests.bind?.(loadState);
researchRuns.bind?.(loadState);
researchLogs.bind?.(loadState);
designTasks.bind?.(loadState);
buildAttempts.bind?.(loadState);

routeFromHash();
loadState();
