import { appState } from "./state.js";

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
  document.querySelector("#breadcrumbGroup").textContent = titleInfo.group;

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

export function registerViews(map) {
  viewRenderers = map;
}

const titles = {
  "overview": { title: "概览", group: "核心" },
  "progress": { title: "实时进度", group: "核心" },
  "challenges": { title: "题目分类", group: "核心" },
  "shards": { title: "任务列表", group: "核心" },
  "logs": { title: "运行日志", group: "核心" },
  "seeds": { title: "种子配置", group: "待删除" },
  "research-submit": { title: "新建需求", group: "研究" },
  "research-requests": { title: "需求管理", group: "研究" },
  "design-tasks": { title: "Design Tasks", group: "研究" },
  "research-runs": { title: "运行记录", group: "研究" },
  "research-logs": { title: "运行日志", group: "研究" },
};

let viewRenderers = {};
