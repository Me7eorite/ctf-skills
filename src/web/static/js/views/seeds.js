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
    <form id="seedForm" class="card card-body">
      <div style="display: flex; align-items: start; justify-content: space-between; gap: var(--space-md); margin-bottom: var(--space-lg);">
        <div>
          <div id="seedFormTitle" class="card-title">新增题目种子</div>
          <p class="card-subtitle">保存 matrix 兼容的生成参数，不会立即启动 Worker。</p>
        </div>
        <button id="resetSeedButton" type="button" style="font-size: var(--font-sm); font-weight: 500; color: var(--ink-500);">清空</button>
      </div>
      <div class="input-group input-group-2">
        <label class="label">题目 ID
          <input id="seedId" required placeholder="web-0001" class="input" style="margin-top: 6px;">
        </label>
        <label class="label">题目名称
          <input id="seedTitle" required placeholder="Login Leak" class="input" style="margin-top: 6px;">
        </label>
        <label class="label">类别
          <select id="seedCategory" class="select" style="margin-top: 6px;">
            <option value="web">Web</option><option value="pwn">Pwn</option><option value="re">Reverse</option>
          </select>
        </label>
        <label class="label">难度
          <select id="seedDifficulty" class="select" style="margin-top: 6px;">
            <option value="easy">Easy</option><option value="medium">Medium</option>
            <option value="hard">Hard</option><option value="expert">Expert</option>
          </select>
        </label>
        <label class="label">分值
          <input id="seedPoints" required type="number" min="1" value="100" class="input" style="margin-top: 6px;">
        </label>
        <label class="label">服务端口
          <input id="seedPort" type="number" min="1" max="65535" value="8080" class="input" style="margin-top: 6px;">
        </label>
        <label class="label" style="grid-column: span 2;">模板
          <input id="seedTemplate" placeholder="web-sqli-basic" class="input" style="margin-top: 6px;">
        </label>
        <label class="label" style="grid-column: span 2;">核心考点
          <input id="seedTechnique" required placeholder="SQL injection login bypass" class="input" style="margin-top: 6px;">
        </label>
        <label class="label" style="grid-column: span 2;">学习目标
          <textarea id="seedObjective" required rows="3" placeholder="玩家完成题目后应掌握什么" class="textarea" style="margin-top: 6px;"></textarea>
        </label>
        <label class="label" style="grid-column: span 2;">高级 JSON
          <textarea id="seedAdvanced" rows="7" placeholder='{"runtime":"node","framework":"Express","deployment":"http/docker"}' class="textarea input-mono" style="margin-top: 6px;"></textarea>
          <span class="label-hint">用于 runtime、framework、compiler、mitigations、target_platform 等类别特有字段。</span>
        </label>
      </div>
      <button type="submit" class="btn btn-primary btn-block" style="margin-top: var(--space-lg);">
        <i data-lucide="save"></i>保存种子
      </button>
    </form>
  `;
}

function seedItemHtml(seed) {
  return `
    <article class="card card-compact">
      <div style="display: flex; flex-wrap: wrap; align-items: start; justify-content: space-between; gap: var(--space-md);">
        <div style="flex: 1; min-width: 0;">
          <div style="display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-sm);">
            <span style="font-size: var(--font-md); font-weight: 600; font-family: var(--font-mono-family);">${escapeHtml(seed.id)}</span>
            ${softPill(categoryLabel(seed.category), categoryTone(seed.category))}
            ${softPill(seed.difficulty, "text-ink-700 bg-ink-100")}
          </div>
          <h3 style="font-size: var(--font-md); font-weight: 500; margin-top: var(--space-md);">${escapeHtml(seed.title)}</h3>
          <p style="font-size: var(--font-md); line-height: 1.5; color: var(--ink-500); margin-top: 2px;">${escapeHtml(seed.learning_objective)}</p>
          <div style="display: flex; flex-wrap: wrap; gap: var(--space-md); margin-top: var(--space-md); font-size: var(--font-sm); color: var(--ink-500);">
            <span>${escapeHtml(seed.primary_technique)}</span>
            <span>${escapeHtml(seed.points)} 分</span>
            ${seed.port ? `<span>端口 ${escapeHtml(seed.port)}</span>` : ""}
          </div>
        </div>
        <div class="btn-group">
          <button class="btn btn-icon btn-secondary edit-seed" data-id="${escapeHtml(seed.id)}" title="编辑">
            <i data-lucide="pencil"></i>
          </button>
          <button class="btn btn-icon" style="border-color: var(--accent-red-border); color: var(--accent-red); background: var(--accent-red-light);" data-id="${escapeHtml(seed.id)}" title="删除">
            <i data-lucide="trash-2"></i>
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
    <div style="display: grid; gap: var(--space-lg);">
      ${seedFormHtml()}
      <div style="min-width: 0;">
        <div class="card card-compact" style="margin-bottom: var(--space-md); display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--space-md);">
          <div>
            <div class="card-title">已配置种子</div>
            <p style="font-size: var(--font-sm); color: var(--ink-500); margin-top: 2px;">
              <span>${seeds.length}</span> 个种子，保存后可生成待处理分片
            </p>
          </div>
          <div class="btn-group">
            <label style="font-size: var(--font-sm); color: var(--ink-500); display: flex; align-items: center; gap: var(--space-sm);">
              每分片
              <input id="seedShardSize" type="number" min="1" value="5" class="input" style="width: 64px; text-align: center;">
            </label>
            <button id="enqueueSeedsButton" type="button" class="btn btn-success">
              <i data-lucide="layers-3"></i>生成分片
            </button>
          </div>
        </div>
        <div style="display: flex; flex-direction: column; gap: var(--space-md);">
          ${seeds.length
            ? seeds.map(seedItemHtml).join("")
            : `<div class="empty">还没有种子，请先在上方配置第一道题</div>`
          }
        </div>
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
  port.style.background = reverse ? "var(--ink-100)" : "var(--surface)";
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