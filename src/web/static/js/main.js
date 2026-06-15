import { appState, scheduleRefresh } from "./state.js";
import { api, postJson } from "./api.js";
import { showToast } from "./ui/toast.js";
import { registerViews, setView, jumpTo } from "./router.js";

import * as overview   from "./views/overview.js";
import * as progress   from "./views/progress.js";
import * as seeds      from "./views/seeds.js";
import * as challenges from "./views/challenges.js";
import * as shards     from "./views/shards.js";
import * as logs       from "./views/logs.js";
import * as research   from "./views/research.js";

const views = { overview, progress, seeds, challenges, shards, logs, research };

registerViews({
  overview:   overview.render,
  progress:   progress.render,
  seeds:      seeds.render,
  challenges: challenges.render,
  shards:     shards.render,
  logs:       logs.render,
  research:   research.render,
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
  if (label) label.textContent = proc.message || "空闲";
  if (bar) bar.className = `h-full transition-all ${proc.running ? "w-full animate-pulse bg-amber-500" : "w-0 bg-emerald-500"}`;
  ["workerButton", "validateButton", "mobileWorkerButton", "mobileValidateButton"].forEach((id) => {
    const button = document.querySelector(`#${id}`);
    if (!button) return;
    button.disabled = !!proc.running;
    button.classList.toggle("opacity-50", !!proc.running);
    button.classList.toggle("pointer-events-none", !!proc.running);
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

// ---- Global event wiring (header + sidebar) ----

document.addEventListener("click", (event) => {
  const nav = event.target.closest(".nav-item");
  if (nav) { setView(nav.dataset.target); return; }
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

// ---- View-local event binding ----

challenges.bind?.(loadState);
seeds.bind?.(loadState);
shards.bind?.(loadState);
logs.bind?.(loadState);

// ---- Boot ----

loadState();
