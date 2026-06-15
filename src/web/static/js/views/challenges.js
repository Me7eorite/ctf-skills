import { appState } from "../state.js";
import { escapeHtml, categoryLabel, categoryTone, statusIndicator, softPill } from "../ui/format.js";

function rowsHtml(rows) {
  if (!rows.length) {
    return `<tr><td colspan="7" class="px-4 py-16 text-center text-[13px] text-ink-500">没有匹配的题目</td></tr>`;
  }
  return rows.map((item) => `
    <tr class="hover:bg-ink-50 transition-colors">
      <td class="px-4 py-3">
        <div class="font-medium text-[13px]">${escapeHtml(item.title)}</div>
        <div class="mt-0.5 text-[11px] text-ink-500 font-mono">${escapeHtml(item.id)}</div>
      </td>
      <td class="px-4 py-3">${softPill(categoryLabel(item.category), categoryTone(item.category))}</td>
      <td class="px-4 py-3 capitalize text-[13px] text-ink-600">${escapeHtml(item.difficulty)}</td>
      <td class="px-4 py-3 text-[13px] text-ink-600">${escapeHtml(item.runtime)} · ${escapeHtml(item.framework)}</td>
      <td class="px-4 py-3">${statusIndicator(item.build_status)}</td>
      <td class="px-4 py-3">${statusIndicator(item.solve_status)}</td>
      <td class="px-4 py-3 text-[11px] text-ink-500 tabular-nums">${escapeHtml(item.updated)}</td>
    </tr>
  `).join("");
}

function filterButton(code, label) {
  const active = appState.category === code;
  return `<button class="filter-button h-9 rounded-md px-3 text-[13px] font-medium transition-colors ${
    active ? "bg-ink-900 text-white" : "border border-line bg-surface text-ink-700 hover:bg-ink-50"
  }" data-category="${code}">${escapeHtml(label)}</button>`;
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
    <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div class="flex flex-wrap gap-2">
        ${filterButton("all", "全部")}
        ${filterButton("web", "Web")}
        ${filterButton("pwn", "Pwn")}
        ${filterButton("re", "Reverse")}
      </div>
      <div class="relative">
        <i data-lucide="search" class="pointer-events-none absolute left-3 top-2.5 size-4 text-ink-400"></i>
        <input id="challengeSearch" value="${escapeHtml(appState.search)}"
          class="h-9 w-full rounded-md border border-line-strong bg-surface pl-9 pr-3 text-[13px] outline-none focus:border-ink-700 sm:w-64"
          placeholder="搜索题目">
      </div>
    </div>
    <div class="card overflow-hidden">
      <div class="overflow-x-auto">
        <table class="w-full min-w-[760px] text-left">
          <thead class="border-b border-line text-[11px] font-medium text-ink-500">
            <tr>
              <th class="px-4 py-3 font-medium">题目</th>
              <th class="px-4 py-3 font-medium">类别</th>
              <th class="px-4 py-3 font-medium">难度</th>
              <th class="px-4 py-3 font-medium">技术栈</th>
              <th class="px-4 py-3 font-medium">构建</th>
              <th class="px-4 py-3 font-medium">EXP</th>
              <th class="px-4 py-3 font-medium">更新</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-line text-[13px]">${rowsHtml(filtered)}</tbody>
        </table>
      </div>
    </div>
  `;
}

export function bind(reload) {
  document.addEventListener("click", (event) => {
    const button = event.target.closest(".filter-button");
    if (!button) return;
    appState.category = button.dataset.category;
    render(appState.data);
    window.lucide?.createIcons();
  });
  document.addEventListener("input", (event) => {
    if (event.target?.id !== "challengeSearch") return;
    appState.search = event.target.value;
    render(appState.data);
    window.lucide?.createIcons();
    document.querySelector("#challengeSearch")?.focus();
  });
}
