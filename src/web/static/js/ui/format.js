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
  re:  { label: "Reverse", tone: "text-amber-700 bg-amber-50" },
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
  return `<span class="inline-flex items-center text-[12px] text-ink-700"><span class="dot ${tone}"></span>${escapeHtml(status || "unknown")}</span>`;
}

/** Soft pill (for categories etc.) */
export function softPill(text, tone = "text-ink-700 bg-ink-100") {
  return `<span class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${tone}">${escapeHtml(text)}</span>`;
}
