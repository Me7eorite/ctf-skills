import { appState } from "./state.js";

const titles = {
  overview: "生产概览",
  progress: "实时进度",
  seeds: "种子配置",
  challenges: "题目",
  shards: "任务分片",
  logs: "运行日志",
  research: "研究请求",
};

let viewRenderers = {};

export function registerViews(map) {
  viewRenderers = map;
}

export function setView(view) {
  if (!titles[view]) view = "overview";
  appState.view = view;

  document.querySelectorAll(".view").forEach((node) => {
    node.classList.toggle("active", node.dataset.view === view);
  });
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.target === view);
  });
  document.querySelector("#pageTitle").textContent = titles[view];
  document.querySelector("#breadcrumb").textContent = titles[view];

  if (window.innerWidth < 1024) {
    document.querySelector("#sidebarNav")?.classList.add("hidden");
  }

  const renderer = viewRenderers[view];
  if (renderer && appState.data) renderer(appState.data);
  window.lucide?.createIcons();
}

export function jumpTo(target) {
  if (target) setView(target);
}
