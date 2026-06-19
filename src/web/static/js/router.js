import { appState } from "./state.js";
import { initIcons } from "./ui/icons.js";

export function setView(view) {
  if (!titles[view]) view = "overview";
  appState.view = view;

  document.querySelectorAll(".view").forEach((node) => {
    node.classList.toggle("active", node.dataset.view === view);
  });
  document.querySelectorAll(".sidebar-nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.target === view);
  });

  const titleInfo = titles[view];
  document.querySelector("#pageTitle").textContent = titleInfo.title;
  document.querySelector("#breadcrumb").textContent = titleInfo.title;

  const groupEl = document.querySelector("#breadcrumbGroup");
  if (titleInfo.group) {
    groupEl.textContent = titleInfo.group;
    groupEl.style.display = "";
    if (groupEl.previousElementSibling) groupEl.previousElementSibling.style.display = "";
  } else {
    groupEl.style.display = "none";
    if (groupEl.previousElementSibling) groupEl.previousElementSibling.style.display = "none";
  }

  const sidebarNav = document.querySelector("#sidebarNav");
  if (window.innerWidth < 1024) {
    sidebarNav?.classList.add("hidden");
  } else {
    sidebarNav?.classList.remove("hidden");
  }

  const renderer = viewRenderers[view];
  if (renderer && appState.data) renderer(appState.data);
  initIcons();
}

export function jumpTo(target) {
  if (target) setView(target);
}

export function registerViews(map) {
  viewRenderers = map;
}

const titles = {
  "overview": { title: "概览", group: "" },
  "research-submit": { title: "新建需求", group: "研究需求" },
  "research-requests": { title: "需求列表", group: "研究需求" },
  "design-tasks": { title: "题目设计", group: "研究需求" },
  "build-attempts": { title: "构建记录", group: "题目管理" },
  "progress": { title: "实时进度", group: "运行监控" },
  "research-runs": { title: "运行记录", group: "运行监控" },
  "research-logs": { title: "研究日志", group: "运行监控" },
  "challenges": { title: "题目列表", group: "题目管理" },
  "shards": { title: "任务列表", group: "题目管理" },
  "seeds": { title: "种子配置", group: "题目管理" },
  "logs": { title: "系统日志", group: "题目管理" },
};

let viewRenderers = {};
