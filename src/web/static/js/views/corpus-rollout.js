import { postJson } from "../api.js";
import { initIcons } from "../ui/icons.js";
import { showToast } from "../ui/toast.js";
import { escapeHtml, softPill } from "../ui/format.js";

const DEFAULT_SHADOW = {
  challenge_count: 40,
  required_vs_observed: { matched: 40 },
  member_decisions: { passed: 36, review_required: 4, blocked: 0 },
  report_uri: "artifacts/governance/current-corpus-shadow.json",
};

const DEFAULT_TRIAL = {
  mode: "trial",
  challenge_count: 20,
  difficulty_distribution: { easy: 8, medium: 8, hard: 4 },
  profile_distribution: {
    "python/container/payload_injection": 10,
    "rust/wasm/runtime_recover": 10,
  },
  design_evidence_passed: 20,
  build_contracts_passed: 20,
  artifact_observations_passed: 20,
  aggregate_decision: "passed",
  member_decisions: { passed: 18, review_required: 2, blocked: 0 },
  blocked_duplicate_count: 0,
  false_positive_review_findings: 0,
};

const state = {
  shadowText: JSON.stringify(DEFAULT_SHADOW, null, 2),
  trialTexts: [
    JSON.stringify({ ...DEFAULT_TRIAL, id: "trial-batch-001" }, null, 2),
    JSON.stringify({ ...DEFAULT_TRIAL, id: "trial-batch-002" }, null, 2),
  ],
  evidence: null,
  error: "",
  loading: false,
};

export function render() {
  const root = document.querySelector('[data-view="corpus-rollout"]');
  if (!root) return;
  root.innerHTML = `
    <div class="layout-content-inner rollout-page">
      <div class="rollout-header">
        <div>
          <div class="rollout-title">治理上线证据</div>
          <div class="rollout-desc">录入 current-corpus shadow 报告和连续 trial 批次报告，由服务端计算 production 门禁。</div>
        </div>
        <div class="rollout-header-actions">
          <button id="rollout-add-trial" type="button" class="btn btn-secondary btn-sm">
            <i data-lucide="plus" class="size-4"></i><span>增加批次</span>
          </button>
          <button id="rollout-evaluate" type="button" class="btn btn-primary btn-sm" ${state.loading ? "disabled" : ""}>
            <i data-lucide="${state.loading ? "loader" : "shield-check"}" class="size-4 ${state.loading ? "spinning" : ""}"></i><span>${state.loading ? "评估中" : "评估门禁"}</span>
          </button>
        </div>
      </div>

      ${state.error ? `
        <div class="rollout-banner rollout-banner-error">
          <i data-lucide="circle-alert" class="size-4"></i>
          <span>${escapeHtml(state.error)}</span>
        </div>
      ` : ""}

      <div class="rollout-grid">
        <section class="card rollout-input-card">
          <div class="card-header">
            <div>
              <div class="card-title">Shadow 报告</div>
              <div class="card-subtitle">当前语料库 required-vs-observed 与相似度统计</div>
            </div>
            ${softPill("shadow", "text-blue-700 bg-blue-50")}
          </div>
          <div class="card-body">
            <textarea id="rollout-shadow" class="textarea input-mono rollout-json" spellcheck="false">${escapeHtml(state.shadowText)}</textarea>
          </div>
        </section>

        <section class="card rollout-input-card">
          <div class="card-header">
            <div>
              <div class="card-title">Trial 批次</div>
              <div class="card-subtitle">至少两批，每批 20 题并包含证据、契约、观察与 corpus 决策</div>
            </div>
            ${softPill(`${state.trialTexts.length} 批`, "text-emerald-700 bg-emerald-50")}
          </div>
          <div class="card-body rollout-trial-stack">
            ${state.trialTexts.map((text, index) => `
              <div class="rollout-trial-editor">
                <div class="rollout-trial-head">
                  <span>Trial ${index + 1}</span>
                  ${state.trialTexts.length > 2 ? `
                    <button type="button" class="btn btn-ghost btn-sm" data-rollout-remove="${index}" title="移除批次">
                      <i data-lucide="trash-2" class="size-4"></i>
                    </button>
                  ` : ""}
                </div>
                <textarea data-rollout-trial="${index}" class="textarea input-mono rollout-json" spellcheck="false">${escapeHtml(text)}</textarea>
              </div>
            `).join("")}
          </div>
        </section>
      </div>

      ${state.evidence ? renderEvidence(state.evidence) : renderEmptyResult()}
    </div>
  `;
  initIcons();
}

export function bind() {
  document.addEventListener("input", (event) => {
    if (event.target?.id === "rollout-shadow") {
      state.shadowText = event.target.value;
      return;
    }
    const trialIndex = event.target?.dataset?.rolloutTrial;
    if (trialIndex !== undefined) {
      state.trialTexts[Number(trialIndex)] = event.target.value;
    }
  });

  document.addEventListener("click", async (event) => {
    if (event.target.closest("#rollout-add-trial")) {
      state.trialTexts.push(
        JSON.stringify({ ...DEFAULT_TRIAL, id: `trial-batch-${String(state.trialTexts.length + 1).padStart(3, "0")}` }, null, 2)
      );
      render();
      return;
    }
    const removeButton = event.target.closest("[data-rollout-remove]");
    if (removeButton) {
      state.trialTexts.splice(Number(removeButton.dataset.rolloutRemove), 1);
      render();
      return;
    }
    if (event.target.closest("#rollout-evaluate")) {
      await evaluateRollout();
    }
  });
}

async function evaluateRollout() {
  state.error = "";
  state.loading = true;
  render();
  try {
    const shadow = parseJsonObject(state.shadowText, "Shadow 报告");
    const trials = state.trialTexts.map((text, index) => parseJsonObject(text, `Trial ${index + 1}`));
    const result = await postJson("/api/corpus/rollout-evidence", {
      shadow_report: shadow,
      trial_reports: trials,
    });
    state.evidence = result.evidence;
    showToast(
      state.evidence.production_mode_allowed
        ? "治理证据通过，可进入人工开启流程"
        : "治理证据未通过，production 保持关闭",
      !state.evidence.production_mode_allowed,
    );
  } catch (error) {
    state.error = error.message;
  } finally {
    state.loading = false;
    render();
  }
}

function parseJsonObject(text, label) {
  let value;
  try {
    value = JSON.parse(text);
  } catch (error) {
    throw new Error(`${label} 不是合法 JSON：${error.message}`);
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} 必须是 JSON object`);
  }
  return value;
}

function renderEmptyResult() {
  return `
    <section class="card rollout-result-card">
      <div class="card-body rollout-empty">
        <i data-lucide="shield" class="size-4"></i>
        <span>等待评估结果</span>
      </div>
    </section>
  `;
}

function renderEvidence(evidence) {
  const gate = evidence.rollout_gate || {};
  const metrics = evidence.acceptance_metrics || {};
  const allowed = Boolean(evidence.production_mode_allowed);
  return `
    <section class="card rollout-result-card">
      <div class="card-header">
        <div>
          <div class="card-title">Production 门禁</div>
          <div class="card-subtitle">${escapeHtml(evidence.status || "")}</div>
        </div>
        ${softPill(
          allowed ? "允许人工开启" : "保持关闭",
          allowed ? "text-emerald-700 bg-emerald-50" : "text-rose-700 bg-rose-50",
        )}
      </div>
      <div class="card-body">
        <div class="rollout-metrics">
          ${metric("连续通过批次", `${gate.consecutive_passed_trial_batches ?? 0} / ${gate.required_consecutive_trial_passes ?? 2}`)}
          ${metric("累计通过题数", gate.cumulative_passed_trial_challenges ?? 0)}
          ${metric("下一检查点", gate.next_checkpoint ?? "完成")}
          ${metric("Pass Rate", percent(metrics.pass_rate))}
          ${metric("Review Rate", percent(metrics.review_rate))}
          ${metric("重复阻断率", percent(metrics.blocked_duplicate_rate))}
        </div>
        ${renderReasons(gate.reasons)}
        <div class="rollout-section-title">Shadow 当前语料库</div>
        ${renderShadow(evidence.shadow_current_corpus || {})}
        <div class="rollout-section-title">Trial 批次</div>
        ${renderTrialTable(evidence.trial_batches || [])}
        <div class="rollout-section-title">Profile 分布</div>
        ${renderDistribution(metrics.profile_distribution || {})}
      </div>
    </section>
  `;
}

function metric(label, value) {
  return `
    <div class="rollout-metric">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `;
}

function renderReasons(reasons) {
  if (!Array.isArray(reasons) || reasons.length === 0) {
    return `<div class="rollout-banner rollout-banner-ok"><i data-lucide="check-circle-2" class="size-4"></i><span>门禁原因为空，证据满足当前策略。</span></div>`;
  }
  return `
    <div class="rollout-banner rollout-banner-error">
      <i data-lucide="circle-alert" class="size-4"></i>
      <span>${reasons.map((reason) => escapeHtml(reason)).join(" · ")}</span>
    </div>
  `;
}

function renderShadow(shadow) {
  return `
    <div class="rollout-shadow-grid">
      ${metric("报告状态", shadow.reported ? "已记录" : "缺失")}
      ${metric("题目数量", shadow.challenge_count ?? 0)}
      ${metric("报告路径", shadow.report_uri || "-")}
    </div>
    ${renderDistribution(shadow.required_vs_observed || {}, "required-vs-observed")}
    ${renderDistribution(shadow.similarity || {}, "similarity")}
  `;
}

function renderTrialTable(trials) {
  if (!trials.length) return `<div class="empty card-body">暂无 trial 批次</div>`;
  return `
    <div class="table-wrap rollout-table-wrap">
      <table class="table rollout-table">
        <thead>
          <tr>
            <th>批次</th>
            <th>题数</th>
            <th>证据</th>
            <th>契约</th>
            <th>观察</th>
            <th>Corpus</th>
            <th>结果</th>
          </tr>
        </thead>
        <tbody>
          ${trials.map((trial) => `
            <tr>
              <td><code>${escapeHtml(trial.id || "-")}</code></td>
              <td>${escapeHtml(trial.challenge_count)}</td>
              <td>${escapeHtml(trial.design_evidence_passed)}</td>
              <td>${escapeHtml(trial.build_contracts_passed)}</td>
              <td>${escapeHtml(trial.artifact_observations_passed)}</td>
              <td>${escapeHtml(trial.aggregate_decision)}</td>
              <td>
                ${softPill(trial.passed ? "通过" : "阻断", trial.passed ? "text-emerald-700 bg-emerald-50" : "text-rose-700 bg-rose-50")}
                ${Array.isArray(trial.reasons) && trial.reasons.length ? `<div class="rollout-row-reasons">${trial.reasons.map((reason) => escapeHtml(reason)).join(" · ")}</div>` : ""}
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderDistribution(distribution, title = "") {
  const entries = Object.entries(distribution || {});
  if (!entries.length) return title ? `<div class="rollout-dist-title">${escapeHtml(title)}: -</div>` : `<div class="rollout-dist-empty">暂无分布数据</div>`;
  return `
    <div class="rollout-dist">
      ${title ? `<div class="rollout-dist-title">${escapeHtml(title)}</div>` : ""}
      <div class="rollout-dist-list">
        ${entries.map(([key, value]) => `
          <span class="rollout-dist-item"><code>${escapeHtml(key)}</code><strong>${escapeHtml(value)}</strong></span>
        `).join("")}
      </div>
    </div>
  `;
}

function percent(value) {
  const number = Number(value || 0);
  return `${Math.round(number * 1000) / 10}%`;
}
