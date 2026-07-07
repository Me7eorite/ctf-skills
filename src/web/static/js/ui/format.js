export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export const categoryMeta = {
  web: { label: "Web", tone: "text-cyan-700 bg-cyan-50" },
  pwn: { label: "Pwn", tone: "text-rose-700 bg-rose-50" },
  re:  { label: "RE", tone: "text-amber-700 bg-amber-50" },
};

export function categoryLabel(code) {
  return categoryMeta[code]?.label || code || "-";
}

export function categoryTone(code) {
  return categoryMeta[code]?.tone || "text-ink-700 bg-ink-100";
}

export const stageLabels = {
  queued: "排队",
  design: "设计",
  implement: "实现",
  build: "构建",
  validate: "验证",
  document: "文档",
  complete: "完成",
};

export function stageLabel(stage) {
  return stageLabels[stage] || stage || "-";
}

/** Maps a status string to a dot tone class. */
export function dotTone(status) {
  if (status === "passed" || status === "done" || status === "completed") return "dot-ok";
  if (status === "failed") return "dot-err";
  if (status === "running" || status === "queued") return "dot-warn";
  return "dot-idle";
}

/** Renders an inline status indicator: a coloured dot + the status text. */
export function statusIndicator(status) {
  const tone = dotTone(status);
  return `<span class="inline-flex items-center text-[12px] text-ink-700"><span class="dot ${tone}"></span>${escapeHtml(runStatusLabel(status))}</span>`;
}

/** Soft pill (for categories etc.) */
export function softPill(text, tone = "text-ink-700 bg-ink-100") {
  return `<span class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${tone}">${escapeHtml(text)}</span>`;
}

export const requestStatusMeta = {
  draft: { label: "草稿", tone: "text-ink-700 bg-ink-100" },
  queued: { label: "等待研究", tone: "text-amber-700 bg-amber-50" },
  researching: { label: "研究中", tone: "text-blue-700 bg-blue-50" },
  researched: { label: "研究完成", tone: "text-emerald-700 bg-emerald-50" },
  failed: { label: "研究失败", tone: "text-rose-700 bg-rose-50" },
};

export function requestStatusPill(status) {
  const meta = requestStatusMeta[status] || { label: status || "未知", tone: "text-ink-700 bg-ink-100" };
  return softPill(meta.label, meta.tone);
}

const RUN_STATUS_LABELS = {
  queued: "等待执行",
  running: "执行中",
  completed: "已完成",
  failed: "失败",
  idle: "空闲",
};

export function runStatusLabel(status) {
  return RUN_STATUS_LABELS[status] || status || "未知";
}

const DIFFICULTY_LABELS = {
  easy: "简单",
  medium: "中等",
  hard: "困难",
  expert: "专家",
};

export function difficultyLabel(difficulty) {
  return DIFFICULTY_LABELS[difficulty] || difficulty || "未知";
}

export function failureMeta(category) {
  const meta = {
    timeout: { icon: "timer-off", tone: "danger" },
    lease_expired: { icon: "clock-alert", tone: "warning" },
    parse_failure: { icon: "braces", tone: "danger" },
    quality_gate: { icon: "badge-alert", tone: "warning" },
    field_validation: { icon: "list-checks", tone: "danger" },
    binding: { icon: "plug-zap", tone: "warning" },
    runtime: { icon: "terminal", tone: "danger" },
    cancelled: { icon: "circle-slash", tone: "muted" },
    unknown: { icon: "circle-help", tone: "muted" },
  };
  return meta[category] || meta.unknown;
}

export const designTaskStatusMeta = {
  draft: { label: "草稿", tone: "text-ink-700 bg-ink-100", stage: "plan" },
  queued: { label: "等待设计", tone: "text-amber-700 bg-amber-50", stage: "design" },
  designing: { label: "设计中", tone: "text-blue-700 bg-blue-50", stage: "design" },
  designed: { label: "设计完成", tone: "text-emerald-700 bg-emerald-50", stage: "design" },
  failed: { label: "设计失败", tone: "text-rose-700 bg-rose-50", stage: "design" },
  archived: { label: "已归档", tone: "text-ink-700 bg-ink-100", stage: "plan" },
  building: { label: "构建中", tone: "text-blue-700 bg-blue-50", stage: "build" },
  built: { label: "构建完成", tone: "text-emerald-700 bg-emerald-50", stage: "build" },
  build_failed: { label: "构建失败", tone: "text-rose-700 bg-rose-50", stage: "build" },
};

export function designTaskStatusLabel(status) {
  return designTaskStatusMeta[status]?.label || status || "未知";
}

export function designTaskStatusPill(status) {
  const meta = designTaskStatusMeta[status] || {
    label: status || "未知",
    tone: "text-ink-700 bg-ink-100",
  };
  return softPill(meta.label, meta.tone);
}

export function designTaskStage(status) {
  return designTaskStatusMeta[status]?.stage || "plan";
}

export function designErrorMessage(error) {
  if (!error) return "未提供失败原因";
  if (error.includes("exhausted")) return "已达到最大设计尝试次数";
  if (error.includes("expected queued")) return "当前任务不在等待设计状态";
  if (error.includes("profile_not_bound")) return "未绑定题目设计 Agent 配置";
  if (error.includes("profile_disabled")) return "题目设计 Agent 配置已停用";
  if (error.includes("timeout")) return "题目设计执行超时";
  if (error.includes("quality_gate")) return "题目设计未通过质量检查";
  return error;
}

export function researchErrorMessage(error) {
  if (!error) return "未提供失败原因";
  if (error === "generation_request_busy" || error === "request_busy") return "当前研究需求正在处理中，请稍后重试";
  if (error === "generation request is busy") return "当前研究需求正在处理中，请稍后重试";
  if (error === "profile_not_bound") return "未绑定研究 Agent 配置";
  if (error.startsWith("profile_disabled:")) {
    return `研究 Agent 配置已停用（${error.slice("profile_disabled:".length)}）`;
  }
  if (error === "worker_startup_failed") return "研究 Worker 启动失败";
  if (error === "already_researched") return "研究已完成，无需重复启动";
  if (error === "no_runnable_run") return "当前没有可执行或可恢复的研究任务";
  if (error === "final_failure_no_retry_left") return "已达到最大尝试次数";
  if (error === "design_task_persistence_failed") return "设计任务写入失败，请释放数据库空间后重试";
  if (error.startsWith("insufficient_findings")) return "有效研究结论不足，未通过质量检查";
  if (error.startsWith("content_hash_dup:")) return "研究来源存在重复内容";
  if (error.startsWith("url_shape_invalid")) return "研究来源 URL 格式无效";
  return error;
}

export function formatDateTime(value) {
  if (!value) return "-";
  const raw = String(value);
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(raw)
    ? raw.replace(" ", "T") + "+08:00"
    : raw;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 19);
  return date.toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
