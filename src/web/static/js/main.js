import { appState, scheduleRefresh } from "./state.js";
import { api, postJson } from "./api.js";
import { showToast } from "./ui/toast.js";
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
});

async function loadState() {
  const refreshIcon = document.querySelector("#refreshIcon");
  refreshIcon?.classList.add("spinning");
  try {
    appState.data = await api("/api/state");
    renderAll();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    refreshIcon?.classList.remove("spinning");
    scheduleRefresh(loadState);
  }
}

function renderAll() {
  document.querySelector("#updatedAt").textContent = `最后同步 ${appState.data.updated_at}`;
  document.querySelector("#updatedAt").classList.remove("hidden");
  for (const view of Object.values(views)) view.render?.(appState.data);
  renderProcess();
  setView(appState.view);
  window.lucide?.createIcons();
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
  ["workerButton", "validateButton", "mobileWorkerButton", "mobileValidateButton"].forEach((id) => {
    const button = document.querySelector(`#${id}`);
    if (!button) return;
    button.disabled = !!proc.running;
    button.classList.toggle("disabled", !!proc.running);
  });
}

async function runAction(kind) {
  try {
    const result = await postJson(`/api/actions/${kind}`);
    showToast(result.message);
    if (kind === "worker") setView("progress");
    await loadState();
  } catch (error) {
    showToast(error.message, true);
  }
}

document.addEventListener("click", (event) => {
  const nav = event.target.closest(".sidebar-nav-item");
  if (nav) {
    setView(nav.dataset.target);
    return;
  }
  const jump = event.target.closest("[data-jump]");
  if (jump) jumpTo(jump.dataset.jump);
});

document.querySelector("#mobileMenu")?.addEventListener("click", () => {
  document.querySelector("#sidebarNav")?.classList.toggle("hidden");
});
document.querySelector("#refreshButton")?.addEventListener("click", loadState);
document.querySelector("#workerButton")?.addEventListener("click", () => runAction("worker"));
document.querySelector("#validateButton")?.addEventListener("click", () => runAction("validate"));
document.querySelector("#mobileWorkerButton")?.addEventListener("click", () => runAction("worker"));
document.querySelector("#mobileValidateButton")?.addEventListener("click", () => runAction("validate"));

challenges.bind?.(loadState);
seeds.bind?.(loadState);
shards.bind?.(loadState);
logs.bind?.(loadState);
researchSubmit.bind?.(loadState);
researchRequests.bind?.(loadState);
researchRuns.bind?.(loadState);
researchLogs.bind?.(loadState);

loadState();