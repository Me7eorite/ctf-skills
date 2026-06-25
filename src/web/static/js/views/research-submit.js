import { api, postJson } from "../api.js";
import { initIcons } from "../ui/icons.js";
import { showToast } from "../ui/toast.js";
import { escapeHtml } from "../ui/format.js";

const state = {
  categories: null,
  showAdvanced: false,
  form: {
    category: "",
    topic: "",
    target_count: 5,
    seed_urls: "",
    search_keywords: "",
    max_attempts: 3,
    distribution: { easy: 0, medium: 0, hard: 0, expert: 0 },
    submitting: false,
    lastResult: null,
    lastError: null,
  },
  flags: {},
};

const DIFFICULTY_LABELS = ["easy", "medium", "hard", "expert"];
const DIFFICULTY_COLORS = { easy: "var(--accent-green)", medium: "var(--brand-500)", hard: "var(--accent-amber)", expert: "var(--accent-red)" };

async function ensureCategories() {
  if (state.categories !== null) return;
  if (state.flags.categories?.loading) return;
  state.flags.categories = { loading: true, error: null };
  try {
    state.categories = await api("/api/research/categories");
    state.flags.categories = { loading: false, error: null };
  } catch (err) {
    state.flags.categories = { loading: false, error: err.message };
  }
  render(state.data);
  initIcons();
}

export function render(data) {
  state.data = data;
  ensureCategories();
  const root = document.querySelector('[data-view="research-submit"]');
  if (!root) return;

  const cats = state.categories || [];
  const f = state.form;
  const distSum = Object.values(f.distribution).reduce((s, v) => s + (Number(v) || 0), 0);
  const target = Number(f.target_count);
  const distMatch = distSum === target && distSum > 0;
  const proc = data?.process || {};
  const keywordCount = parseLines(f.search_keywords).length;

  root.innerHTML = `
    <div class="layout-content-inner">
      <!-- === 主内容区 === -->
      <div class="layout-content-main">

        <!-- 页面标题 -->
        <div class="rs-page-header">
          <div class="rs-page-title">新建研究需求</div>
          <div class="rs-page-desc">提交后立即入队，Hermes 由 Worker 异步生成。填写完成后点击底部提交即可。</div>
        </div>

        <!-- --- 结果反馈 --- -->
        ${(f.lastResult || f.lastError) ? `
          <div style="margin-bottom: var(--space-md);">
            ${f.lastResult ? `
              <div class="success-banner">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#16a34a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
                <span>已入队 — request <code style="font-family: var(--font-mono-family); font-size: 11px;">${escapeHtml(f.lastResult.request.id.slice(0, 8))}&hellip;</code></span>
              </div>
            ` : ""}
            ${f.lastError ? `
              <div class="error-banner">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#dc2626" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                <span>${escapeHtml(f.lastError)}</span>
              </div>
            ` : ""}
          </div>
        ` : ""}

        <form id="research-submit-form">
        <!-- === 步骤 1: 基本信息 === -->
        <div class="rs-step-card">
          <div class="rs-step-header">
            <span class="rs-step-badge info">1</span>
            <div class="rs-step-meta">
              <div class="rs-step-title">基本信息</div>
              <div class="rs-step-desc">选择分类，定义话题和目标数量</div>
            </div>
            <span class="rs-step-status ${f.category && f.topic ? 'match' : ''}">
              ${f.category && f.topic ? '已填写' : '待填写'}
            </span>
          </div>
          <div class="rs-step-body">
            <div class="input-group">
              <label>
                <span class="label">类别</span>
                <select id="form-category" required class="select">
                  <option value="">选择类别</option>
                  ${cats.map(c => `<option value="${escapeHtml(c.code)}"${f.category === c.code ? " selected" : ""}>${escapeHtml(c.code)} \u00B7 ${escapeHtml(c.display_name)}</option>`).join("")}
                </select>
              </label>
              <label>
                <span class="label">话题</span>
                <input id="form-topic" required value="${escapeHtml(f.topic)}" class="input" placeholder="如: SQL injection bypass via UNION select" />
              </label>
              <div class="input-group input-group-2">
                <label>
                  <span class="label">目标数量</span>
                  <input id="form-target-count" type="number" min="1" value="${target}" required class="input" />
                </label>
                <label>
                  <span class="label">最大重试次数</span>
                  <input id="form-max-attempts" type="number" min="1" value="${f.max_attempts}" class="input" />
                </label>
              </div>
            </div>
          </div>
        </div>

        <!-- === 步骤 2: 难度分布 === -->
        <div class="rs-step-card">
          <div class="rs-step-header">
            <span class="rs-step-badge warn">2</span>
            <div class="rs-step-meta">
              <div class="rs-step-title">难度分布</div>
              <div class="rs-step-desc">分配各难度等级的目标数量，合计必须等于目标数量</div>
            </div>
            <span class="rs-step-status ${distMatch ? 'match' : (distSum > 0 ? 'mismatch' : '')}">
              合计 ${distSum} / 目标 ${target}
            </span>
          </div>
          <div class="rs-step-body">
            <div class="rs-diff-grid">
              ${DIFFICULTY_LABELS.map(label => `
                <div class="rs-diff-card">
                  <input data-difficulty="${label}" type="number" min="0" value="${f.distribution[label] || 0}" />
                  <span class="rs-diff-label">${label}</span>
                  <span class="rs-diff-count" style="color: ${DIFFICULTY_COLORS[label]}">${f.distribution[label] || 0} 题</span>
                </div>
              `).join("")}
            </div>
            ${!distMatch && distSum > 0 ? `
              <div class="rs-dist-warning">
                ${distSum < target ? `还需要 ${target - distSum} 个题目` : `超出目标 ${distSum - target} 个题目`}
              </div>
            ` : ""}
          </div>
        </div>

        <!-- === 步骤 3: 高级选项 === -->
        <div class="rs-step-card" id="advanced-toggle">
          <div class="rs-step-header rs-collapse-toggle">
            <span class="rs-step-badge ok">3</span>
            <div class="rs-step-meta">
              <div class="rs-step-title">高级选项</div>
              <div class="rs-step-desc">考点关键字、种子 URL 与其它可选参数</div>
            </div>
            <i data-lucide="${state.showAdvanced ? 'chevron-up' : 'chevron-down'}" class="rs-collapse-chevron ${state.showAdvanced ? 'open' : ''}"></i>
          </div>
          <div id="advanced-panel" class="rs-step-body" style="${state.showAdvanced ? "" : "display: none;"}">
            <label>
              <span class="label">考点关键字（可选，每行一个）</span>
              <textarea id="form-search-keywords" rows="3"
                placeholder="JWT kid header traversal&#10;JWKS cache poisoning&#10;prototype pollution"
                class="textarea input-mono">${escapeHtml(f.search_keywords)}</textarea>
              <span class="label-hint">Hermes 会用“话题 + 关键字”组合检索网页资料，并把采用的来源写入 sources[]</span>
            </label>
            <label>
              <span class="label">种子 URL（可选，每行一个）</span>
              <textarea id="form-seed-urls" rows="3"
                placeholder="https://owasp.org/Top10/&#10;https://portswigger.net/web-security"
                class="textarea input-mono">${escapeHtml(f.seed_urls)}</textarea>
              <span class="label-hint">提供参考 URL 可帮助 Hermes 生成更精准的题目</span>
            </label>
          </div>
        </div>

        <!-- === Sticky Action Bar === -->
        <div class="rs-action-bar">
          <div class="rs-action-summary">
            目标 <span class="rs-action-summary-count">${target}</span>
            <span class="rs-action-summary-sep">·</span>
            已分配 <span class="rs-action-summary-count">${distSum}</span>
            ${distMatch ? '<span style="color: var(--accent-green); margin-left: 4px;">&#10003; 匹配</span>' : (distSum > 0 ? '<span style="color: var(--accent-red); margin-left: 4px;">&#10007; 不匹配</span>' : '')}
          </div>
          <div class="rs-action-buttons">
            <button id="form-reset" type="button" class="btn btn-secondary btn-sm">
              <i data-lucide="rotate-ccw" style="width: 14px; height: 14px;"></i>
              <span>重置</span>
            </button>
            <button type="submit" ${f.submitting ? "disabled" : ""} class="btn btn-primary">
              <i data-lucide="${f.submitting ? 'loader' : 'send'}" class="${f.submitting ? 'spinning' : ''}" style="width: 16px; height: 16px;"></i>
              ${f.submitting ? '提交中\u2026' : '提交需求'}
            </button>
          </div>
        </div>
        </form>

      </div>

      <!-- === 右侧面板 === -->
      <div class="layout-content-side">
        <div class="rs-side-panel">

          <!-- 任务摘要 -->
          <div class="rs-side-card">
            <div class="rs-side-card-title">
              <i data-lucide="clipboard-list"></i>
              任务摘要
            </div>
            <div class="rs-summary-item">
              <span class="rs-summary-label">类别</span>
              <span class="rs-summary-value">${f.category || '\u2014'}</span>
            </div>
            <div class="rs-summary-item">
              <span class="rs-summary-label">目标数量</span>
              <span class="rs-summary-value">${target}</span>
            </div>
            <div class="rs-summary-item">
              <span class="rs-summary-label">已分配</span>
              <span class="rs-summary-value">${distSum}</span>
            </div>
            <div class="rs-summary-item">
              <span class="rs-summary-label">难度层级</span>
              <span class="rs-summary-value">${Object.entries(f.distribution).filter(([_, v]) => Number(v) > 0).length || 0} / 4</span>
            </div>
            <div class="rs-summary-item">
              <span class="rs-summary-label">种子 URL</span>
              <span class="rs-summary-value">${parseLines(f.seed_urls).length || 0} 条</span>
            </div>
            <div class="rs-summary-item">
              <span class="rs-summary-label">考点关键字</span>
              <span class="rs-summary-value">${keywordCount} 条</span>
            </div>
            <div class="rs-summary-item">
              <span class="rs-summary-label">最大重试</span>
              <span class="rs-summary-value">${f.max_attempts}</span>
            </div>
          </div>

          <!-- Worker 状态 -->
          <div class="rs-side-card">
            <div class="rs-side-card-title">
              <i data-lucide="cpu"></i>
              Worker 状态
            </div>
            <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 12px;">
              <span style="width: 8px; height: 8px; border-radius: 999px; flex-shrink: 0; background: ${proc.running ? 'var(--accent-amber)' : 'var(--accent-green)'}; ${proc.running ? 'animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;' : ''}"></span>
              <span style="font-size: var(--font-sm); font-weight: 500; color: var(--ink-700);">${proc.running ? '运行中' : '空闲'}</span>
            </div>
            <div style="height: 4px; border-radius: 999px; background: var(--ink-200); overflow: hidden;">
              <div style="height: 100%; border-radius: 999px; width: ${proc.running ? '100%' : '0'}; background: ${proc.running ? 'var(--accent-amber)' : 'var(--accent-green)'}; transition: width var(--transition-normal) ease;"></div>
            </div>
          </div>

          <!-- 提交前检查 -->
          <div class="rs-side-card">
            <div class="rs-side-card-title">
              <i data-lucide="check-circle-2"></i>
              提交前检查
            </div>
            <div class="rs-checklist-item ${f.category ? 'done' : ''}">
              <span class="dot ${f.category ? 'ok' : 'info'}"></span>
              已选择类别
            </div>
            <div class="rs-checklist-item ${f.topic ? 'done' : ''}">
              <span class="dot ${f.topic ? 'ok' : 'info'}"></span>
              已填写话题
            </div>
            <div class="rs-checklist-item ${target > 0 ? 'done' : ''}">
              <span class="dot ${target > 0 ? 'ok' : 'info'}"></span>
              已设置目标数量
            </div>
            <div class="rs-checklist-item ${distMatch ? 'done' : (distSum > 0 ? '' : '')}">
              <span class="dot ${distMatch ? 'ok' : (distSum > 0 ? 'warn' : 'info')}"></span>
              难度分布匹配
            </div>
          </div>

        </div>
      </div>
    </div>
  `;

  initIcons();
}

async function handleSubmit() {
  const f = state.form;
  const seedList = parseLines(f.seed_urls);
  const searchKeywords = parseLines(f.search_keywords);
  const dist = Object.fromEntries(Object.entries(f.distribution).filter(([_, v]) => Number(v) > 0));
  const runtimeConstraints = {};
  if (searchKeywords.length > 0) {
    runtimeConstraints.search_keywords = searchKeywords;
  }

  f.submitting = true;
  f.lastResult = null;
  f.lastError = null;
  render(state.data);
  initIcons();

  try {
    const result = await postJson("/api/research/requests", {
      category: f.category,
      topic: f.topic,
      target_count: Number(f.target_count),
      difficulty_distribution: dist,
      seed_urls: seedList,
      max_attempts: Number(f.max_attempts),
      runtime_constraints: runtimeConstraints,
    });
    f.lastResult = result;
    showToast(`\u5DF2\u5165\u961F\uFF1A${result.request.id.slice(0, 8)}\u2026`);
  } catch (err) {
    f.lastError = err.message || String(err);
  } finally {
    f.submitting = false;
    render(state.data);
    initIcons();
  }
}

function resetForm() {
  state.form = {
    category: "",
    topic: "",
    target_count: 5,
    seed_urls: "",
    search_keywords: "",
    max_attempts: 3,
    distribution: { easy: 0, medium: 0, hard: 0, expert: 0 },
    submitting: false,
    lastResult: null,
    lastError: null,
  };
  state.showAdvanced = false;
  render(state.data);
  initIcons();
}

export function bind() {
  document.addEventListener("submit", (e) => {
    if (e.target?.id === "research-submit-form") {
      e.preventDefault();
      handleSubmit();
    }
  });

  document.addEventListener("click", (e) => {
    if (e.target.closest("#form-reset")) {
      resetForm();
    }
    if (e.target.closest("#advanced-toggle")) {
      state.showAdvanced = !state.showAdvanced;
      render(state.data);
      initIcons();
    }
  });

  document.addEventListener("change", (e) => {
    if (e.target.id === "form-category") {
      state.form.category = e.target.value;
    } else if (e.target.dataset?.difficulty) {
      state.form.distribution[e.target.dataset.difficulty] = Number(e.target.value) || 0;
      render(state.data);
      initIcons();
    }
  });

  document.addEventListener("input", (e) => {
    if (e.target.id === "form-topic") state.form.topic = e.target.value;
    else if (e.target.id === "form-target-count") state.form.target_count = e.target.value;
    else if (e.target.id === "form-max-attempts") state.form.max_attempts = e.target.value;
    else if (e.target.id === "form-seed-urls") state.form.seed_urls = e.target.value;
    else if (e.target.id === "form-search-keywords") state.form.search_keywords = e.target.value;
  });
}

function parseLines(value) {
  return String(value || "").split("\n").map(s => s.trim()).filter(s => s.length > 0);
}
