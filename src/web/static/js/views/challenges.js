import { appState } from "../state.js";
import { initIcons } from "../ui/icons.js";
import { escapeHtml, categoryLabel, categoryTone, statusIndicator, softPill } from "../ui/format.js";

function rowsHtml(rows) {
  if (!rows.length) {
    return `<tr><td colspan="7" class="table-empty">没有匹配的题目</td></tr>`;
  }
  return rows.map((item) => `
    <tr>
      <td>
        <div style="font-size: var(--font-md); font-weight: 500;">${escapeHtml(item.title)}</div>
        <div style="font-size: var(--font-sm); color: var(--ink-500); font-family: var(--font-mono-family); margin-top: 2px;">${escapeHtml(item.id)}</div>
      </td>
      <td>${softPill(categoryLabel(item.category), categoryTone(item.category))}</td>
      <td style="text-transform: capitalize; color: var(--ink-600);">${escapeHtml(item.difficulty)}</td>
      <td style="color: var(--ink-600);">${escapeHtml(item.runtime)} · ${escapeHtml(item.framework)}</td>
      <td>${statusIndicator(item.build_status)}</td>
      <td>${statusIndicator(item.solve_status)}</td>
      <td class="table-cell-time">${escapeHtml(item.updated)}</td>
    </tr>
  `).join("");
}

function filterButton(code, label) {
  const active = appState.category === code;
  return `<button class="filter-button${active ? " active" : ""}" data-category="${code}">${escapeHtml(label)}</button>`;
}

export function render(data) {
  const root = document.querySelector('[data-view="challenges"]');
  if (!root) return;
  const query = appState.search.toLowerCase();
  const filtered = (data.challenges || []).filter((item) => {
    const cat = appState.category === "all" || item.category === appState.category;
    return cat && (!query || `${item.id} ${item.title} ${item.runtime} ${item.framework}`.toLowerCase().includes(query));
  });

  root.innerHTML = `
    <div class="filter-bar-responsive" style="margin-bottom: var(--space-md);">
      <div class="filter-buttons">
        ${filterButton("all", "全部")}
        ${filterButton("web", "Web")}
        ${filterButton("pwn", "Pwn")}
        ${filterButton("re", "Reverse")}
      </div>
      <div class="input-icon-wrapper">
        <i data-lucide="search" class="input-icon-left"></i>
        <input id="challengeSearch" value="${escapeHtml(appState.search)}" class="input" placeholder="搜索题目" style="width: 256px;">
      </div>
    </div>
    <div class="table-container">
      <table class="table">
        <thead>
          <tr>
            <th>题目</th>
            <th>类别</th>
            <th>难度</th>
            <th>技术栈</th>
            <th>构建</th>
            <th>EXP</th>
            <th>更新</th>
          </tr>
        </thead>
        <tbody>${rowsHtml(filtered)}</tbody>
      </table>
    </div>
  `;
}

export function bind(reload) {
  document.addEventListener("click", (event) => {
    const button = event.target.closest(".filter-button");
    if (!button) return;
    appState.category = button.dataset.category;
    render(appState.data);
    initIcons();
  });

  document.addEventListener("input", (event) => {
    if (event.target?.id !== "challengeSearch") return;
    appState.search = event.target.value;
    render(appState.data);
    initIcons();
    document.querySelector("#challengeSearch")?.focus();
  });
}