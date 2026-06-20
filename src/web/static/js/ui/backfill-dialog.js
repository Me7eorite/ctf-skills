import { escapeHtml } from "./format.js";

let activeConfirmation = null;
let dialogSequence = 0;

export function confirmBackfill({ preview, error = null }) {
  if (activeConfirmation) return activeConfirmation;

  activeConfirmation = new Promise((resolve) => {
    dialogSequence += 1;
    const titleId = `backfill-dialog-title-${dialogSequence}`;
    const previousFocus = document.activeElement;
    const hasError = error !== null;
    const overlay = document.createElement("div");
    overlay.className = "backfill-dialog-overlay";
    overlay.dataset.backfillDialog = "true";
    overlay.style.cssText = [
      "position: fixed",
      "inset: 0",
      "z-index: 1000",
      "display: grid",
      "place-items: center",
      "background: rgba(15, 23, 42, 0.36)",
      "padding: 20px",
    ].join(";");

    overlay.innerHTML = `
      <div role="dialog" aria-modal="true" aria-labelledby="${titleId}" style="width: min(560px, 100%); border: 1px solid var(--line); border-radius: var(--radius-md); background: var(--paper); box-shadow: var(--shadow-lg);">
        <div style="padding: var(--space-lg); border-bottom: 1px solid var(--line);">
          <div id="${titleId}" style="font-size: var(--font-lg); font-weight: 600; color: var(--ink-800);">从日志恢复研究结果</div>
          <p style="margin-top: var(--space-sm); color: var(--ink-600); font-size: var(--font-md);">预览结果只表示当前日志可解析，候选不保证恢复一定成功。确认后会把结果写入数据库并刷新当前详情。</p>
        </div>
        ${hasError ? `
          <div style="padding: var(--space-md) var(--space-lg); color: var(--accent-red); font-size: var(--font-sm);">
            <strong>预览失败</strong>
            <p style="margin-top: var(--space-xs); overflow-wrap: anywhere;">${escapeHtml(error?.detail || error?.message || "无法预览日志恢复结果")}</p>
          </div>
        ` : `
          <dl style="display: grid; grid-template-columns: minmax(112px, max-content) 1fr; gap: var(--space-sm) var(--space-md); padding: var(--space-md) var(--space-lg); color: var(--ink-700); font-size: var(--font-sm);">
            <dt style="color: var(--ink-500);">日志路径</dt>
            <dd style="margin: 0; overflow-wrap: anywhere; font-family: var(--font-mono-family);">${escapeHtml(preview?.log_path || "")}</dd>
            <dt style="color: var(--ink-500);">日志摘要</dt>
            <dd style="margin: 0; overflow-wrap: anywhere; font-family: var(--font-mono-family);">${escapeHtml(preview?.log_sha256 || "")}</dd>
            <dt style="color: var(--ink-500);">预计来源</dt>
            <dd style="margin: 0;">${escapeHtml(preview?.would_insert_sources ?? 0)} 条</dd>
            <dt style="color: var(--ink-500);">预计结论</dt>
            <dd style="margin: 0;">${escapeHtml(preview?.would_insert_findings ?? 0)} 条</dd>
            <dt style="color: var(--ink-500);">运行状态</dt>
            <dd style="margin: 0;">${escapeHtml(preview?.current_run_status || "")} → ${escapeHtml(preview?.would_run_status || "")}</dd>
            <dt style="color: var(--ink-500);">需求状态</dt>
            <dd style="margin: 0;">${escapeHtml(preview?.current_request_status || "")} → ${escapeHtml(preview?.would_request_status || "")}</dd>
          </dl>
        `}
        <div style="display: flex; justify-content: flex-end; gap: var(--space-sm); padding: var(--space-md) var(--space-lg); border-top: 1px solid var(--line);">
          <button class="btn btn-ghost btn-sm" id="backfill-dialog-cancel">取消</button>
          <button class="btn btn-primary btn-sm" id="backfill-dialog-confirm"${hasError ? " disabled" : ""}>确认恢复</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);

    const cancelButton = overlay.querySelector("#backfill-dialog-cancel");
    const confirmButton = overlay.querySelector("#backfill-dialog-confirm");
    const cleanup = (value) => {
      document.removeEventListener("keydown", onKeyDown);
      overlay.remove();
      activeConfirmation = null;
      if (previousFocus?.isConnected) previousFocus.focus();
      resolve(value);
    };
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        cleanup(false);
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = hasError ? [cancelButton] : [cancelButton, confirmButton];
      const current = focusable.indexOf(document.activeElement);
      const next = event.shiftKey
        ? (current <= 0 ? focusable.length - 1 : current - 1)
        : (current === focusable.length - 1 ? 0 : current + 1);
      event.preventDefault();
      focusable[next].focus();
    };
    cancelButton.addEventListener("click", () => cleanup(false));
    confirmButton.addEventListener("click", () => {
      if (!hasError) cleanup(true);
    });
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) cleanup(false);
    });
    document.addEventListener("keydown", onKeyDown);
    cancelButton.focus();
  });
  return activeConfirmation;
}
