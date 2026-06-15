import { appState } from "../state.js";
import { api, postJson, del } from "../api.js";
import { showToast } from "../ui/toast.js";
import { setView } from "../router.js";
import { escapeHtml, categoryLabel, categoryTone, softPill } from "../ui/format.js";

const SEED_CORE_FIELDS = new Set([
  "id", "title", "category", "difficulty", "points", "port", "template",
  "primary_technique", "learning_objective",
]);

function seedFormHtml() {
  return `
    <form id="seedForm" class="card p-5">
      <div class="mb-5 flex items-start justify-between gap-3">
        <div>
          <div id="seedFormTitle" class="card-title">新增题目种子</div>
          <p class="card-subtitle">保存 matrix 兼容的生成参数，不会立即启动 Worker。</p>
        </div>
        <button id="resetSeedButton" type="button" class="text-[11px] font-medium text-ink-500 hover:text-ink-900">清空</button>
      </div>
      <div class="grid gap-4 sm:grid-cols-2">
        <label class="label">题目 ID
          <input id="seedId" required placeholder="web-0001" class="input mt-1.5">
        </label>
        <label class="label">题目名称
          <input id="seedTitle" required placeholder="Login Leak" class="input mt-1.5">
        </label>
        <label class="label">类别
          <select id="seedCategory" class="select mt-1.5">
            <option value="web">Web</option><option value="pwn">Pwn</option><option value="re">Reverse</option>
          </select>
        </label>
        <label class="label">难度
          <select id="seedDifficulty" class="select mt-1.5">
            <option value="easy">Easy</option><option value="medium">Medium</option>
            <option value="hard">Hard</option><option value="expert">Expert</option>
          </select>
        </label>
        <label class="label">分值
          <input id="seedPoints" required type="number" min="1" value="100" class="input mt-1.5">
        </label>
        <label class="label">服务端口
          <input id="seedPort" type="number" min="1" max="65535" value="8080" class="input mt-1.5">
        </label>
        <label class="label sm:col-span-2">模板
          <input id="seedTemplate" placeholder="web-sqli-basic" class="input mt-1.5">
        </label>
        <label class="label sm:col-span-2">核心考点
          <input id="seedTechnique" required placeholder="SQL injection login bypass" class="input mt-1.5">
        </label>
        <label class="label sm:col-span-2">学习目标
          <textarea id="seedObjective" required rows="3" placeholder="玩家完成题目后应掌握什么" class="textarea mt-1.5"></textarea>
        </label>
        <label class="label sm:col-span-2">高级 JSON
          <textarea id="seedAdvanced" rows="7" placeholder='{"runtime":"node","framework":"Express","deployment":"http/docker"}'
            class="textarea mt-1.5 font-mono text-[12px] leading-5"></textarea>
          <span class="mt-1 block font-normal text-ink-400 text-[10px]">用于 runtime、framework、compiler、mitigations、target_platform 等类别特有字段。</span>
        </label>
      </div>
      <button type="submit" class="mt-5 flex h-10 w-full items-center justify-center gap-2 rounded-md bg-ink-900 px-4 text-[13px] font-medium text-white transition-colors hover:bg-ink-800">
        <i data-lucide="save" class="size-4"></i>保存种子
      </button>
    </form>
  `;
}

function seedItemHtml(seed) {
  return `
    <article class="card p-4">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0 flex-1">
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-mono text-[13px] font-semibold">${escapeHtml(seed.id)}</span>
            ${softPill(categoryLabel(seed.category), categoryTone(seed.category))}
            ${softPill(seed.difficulty, "text-ink-700 bg-ink-100")}
          </div>
          <h3 class="mt-3 text-[13px] font-medium">${escapeHtml(seed.title)}</h3>
          <p class="mt-1 text-[12px] leading-5 text-ink-500">${escapeHtml(seed.learning_objective)}</p>
          <div class="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-500">
            <span>${escapeHtml(seed.primary_technique)}</span>
            <span class="tabular-nums">${escapeHtml(seed.points)} 分</span>
            ${seed.port ? `<span class="tabular-nums">端口 ${escapeHtml(seed.port)}</span>` : ""}
          </div>
        </div>
        <div class="flex gap-2">
          <button class="edit-seed grid size-9 place-items-center rounded-md border border-line text-ink-700 hover:bg-ink-50" data-id="${escapeHtml(seed.id)}" title="编辑">
            <i data-lucide="pencil" class="size-4"></i>
          </button>
          <button class="delete-seed grid size-9 place-items-center rounded-md border border-rose-200 text-rose-600 hover:bg-rose-50" data-id="${escapeHtml(seed.id)}" title="删除">
            <i data-lucide="trash-2" class="size-4"></i>
          </button>
        </div>
      </div>
    </article>
  `;
}

export function render(data) {
  const root = document.querySelector('[data-view="seeds"]');
  if (!root) return;
  const seeds = data.seeds || [];
  root.innerHTML = `
    <div class="grid gap-5 xl:grid-cols-[minmax(340px,.8fr)_minmax(0,1.2fr)]">
      ${seedFormHtml()}
      <div class="min-w-0">
        <div class="mb-4 card p-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div class="card-title">已配置种子</div>
            <p class="mt-1 text-[11px] text-ink-500">
              <span class="tabular-nums">${seeds.length}</span> 个种子，保存后可生成待处理分片
            </p>
          </div>
          <div class="flex items-center gap-2">
            <label class="text-[11px] text-ink-500 flex items-center gap-1.5">每分片
              <input id="seedShardSize" type="number" min="1" value="5"
                class="h-9 w-16 rounded-md border border-line-strong bg-surface px-2 text-[13px] text-ink-900 outline-none focus:border-ink-700">
            </label>
            <button id="enqueueSeedsButton" type="button"
              class="flex h-9 items-center gap-2 rounded-md bg-emerald-600 px-3 text-[13px] font-medium text-white hover:bg-emerald-700">
              <i data-lucide="layers-3" class="size-4"></i>生成分片
            </button>
          </div>
        </div>
        <div class="space-y-3">${
          seeds.length
            ? seeds.map(seedItemHtml).join("")
            : `<div class="empty">还没有种子，请先在左侧配置第一道题</div>`
        }</div>
      </div>
    </div>
  `;
  applyEditingForm();
  updateSeedCategoryFields();
}

function applyEditingForm() {
  if (!appState.editingSeedId) return;
  const seed = appState.data?.seeds?.find((s) => s.id === appState.editingSeedId);
  if (!seed) return;
  setField("#seedId", seed.id, true);
  setField("#seedTitle", seed.title || "");
  setField("#seedCategory", seed.category || "web");
  setField("#seedDifficulty", seed.difficulty || "easy");
  setField("#seedPoints", seed.points || 100);
  setField("#seedPort", seed.port || "");
  setField("#seedTemplate", seed.template || "");
  setField("#seedTechnique", seed.primary_technique || "");
  setField("#seedObjective", seed.learning_objective || "");
  const advanced = Object.fromEntries(Object.entries(seed).filter(([key]) => !SEED_CORE_FIELDS.has(key)));
  setField("#seedAdvanced", Object.keys(advanced).length ? JSON.stringify(advanced, null, 2) : "");
  document.querySelector("#seedFormTitle").textContent = `编辑 ${seed.id}`;
}

function setField(selector, value, disabled = false) {
  const el = document.querySelector(selector);
  if (!el) return;
  el.value = value ?? "";
  if (disabled !== undefined) el.disabled = !!disabled;
}

function updateSeedCategoryFields() {
  const cat = document.querySelector("#seedCategory");
  const port = document.querySelector("#seedPort");
  if (!cat || !port) return;
  const reverse = cat.value === "re";
  port.disabled = reverse;
  port.classList.toggle("bg-ink-100", reverse);
  if (reverse) port.value = "";
  else if (!port.value) port.value = cat.value === "pwn" ? 9001 : 8080;
}

function resetSeedForm() {
  appState.editingSeedId = null;
  const form = document.querySelector("#seedForm");
  if (form) form.reset();
  setField("#seedPoints", 100);
  setField("#seedPort", 8080);
  setField("#seedAdvanced", "");
  const idInput = document.querySelector("#seedId");
  if (idInput) idInput.disabled = false;
  const title = document.querySelector("#seedFormTitle");
  if (title) title.textContent = "新增题目种子";
  updateSeedCategoryFields();
}

async function saveSeed(event, reload) {
  event.preventDefault();
  try {
    const advancedText = document.querySelector("#seedAdvanced").value.trim();
    const advanced = advancedText ? JSON.parse(advancedText) : {};
    const category = document.querySelector("#seedCategory").value;
    const seed = {
      ...advanced,
      id: document.querySelector("#seedId").value.trim(),
      title: document.querySelector("#seedTitle").value.trim(),
      category,
      difficulty: document.querySelector("#seedDifficulty").value,
      points: Number(document.querySelector("#seedPoints").value),
      template: document.querySelector("#seedTemplate").value.trim(),
      primary_technique: document.querySelector("#seedTechnique").value.trim(),
      learning_objective: document.querySelector("#seedObjective").value.trim(),
    };
    const port = Number(document.querySelector("#seedPort").value);
    if (category !== "re" || port) seed.port = port;
    Object.keys(seed).forEach((k) => { if (seed[k] === "") delete seed[k]; });
    await postJson("/api/seeds", seed);
    showToast(`${seed.id} 已保存`);
    resetSeedForm();
    await reload();
  } catch (error) {
    showToast(error instanceof SyntaxError ? "高级 JSON 格式不正确" : error.message, true);
  }
}

async function enqueueSeeds(reload) {
  try {
    const size = Number(document.querySelector("#seedShardSize").value);
    const result = await postJson("/api/seeds/enqueue", { size });
    showToast(result.message);
    setView("shards");
    await reload();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function deleteSeed(id, reload) {
  try {
    const result = await del(`/api/seeds/${encodeURIComponent(id)}`);
    showToast(result.message);
    if (appState.editingSeedId === id) resetSeedForm();
    await reload();
  } catch (error) {
    showToast(error.message, true);
  }
}

export function bind(reload) {
  document.addEventListener("submit", (event) => {
    if (event.target?.id === "seedForm") saveSeed(event, reload);
  });
  document.addEventListener("click", (event) => {
    if (event.target.closest("#resetSeedButton")) { resetSeedForm(); return; }
    if (event.target.closest("#enqueueSeedsButton")) { enqueueSeeds(reload); return; }
    const edit = event.target.closest(".edit-seed");
    if (edit) {
      appState.editingSeedId = edit.dataset.id;
      setView("seeds");
      applyEditingForm();
      document.querySelector("#seedForm")?.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    const remove = event.target.closest(".delete-seed");
    if (remove) deleteSeed(remove.dataset.id, reload);
  });
  document.addEventListener("change", (event) => {
    if (event.target?.id === "seedCategory") updateSeedCategoryFields();
  });
}
