import { appState } from "./state.js";
import { initIcons } from "./ui/icons.js";

export function setView(view) {
  // 兼容重定向：旧入口跳转到新页面
  if (view === "progress") view = "worker-pool";
  if (view === "research-logs") view = "logs";
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

  // 桌面端始终展开导航；移动端的展开/收起由汉堡按钮与导航点击控制，
  // 不在此处强制收起，否则每次轮询刷新都会把已展开的菜单弹回去。
  if (window.innerWidth >= 1024) {
    document.querySelector("#sidebarNav")?.classList.remove("hidden");
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
  "overview":         { title: "概览",     group: "生产管线" },
  "research-submit":  { title: "新建需求", group: "生产管线" },
  "research-requests":{ title: "需求列表", group: "生产管线" },
  "design-tasks":     { title: "题目设计", group: "生产管线" },
  "challenges":       { title: "完成题目", group: "生产管线" },
  "build-attempts":   { title: "构建列表", group: "生产管线" },
  "worker-pool":      { title: "实时进度", group: "运行监控" },
  "logs":             { title: "运行日志", group: "运行监控" },
  "shards":           { title: "任务队列", group: "系统" },
  // 以下保留路由兼容，由 setView 重定向到新页面
  "research-runs":    { title: "运行记录", group: "" },
  "research-logs":    { title: "研究日志", group: "" },
  "progress":         { title: "实时进度", group: "" },
};

let viewRenderers = {};
