import { escapeHtml } from "./format.js";
import { initIcons } from "./icons.js";

let activeConfirmation = null;
let dialogSequence = 0;

export function confirmDeletion({ title, message }) {
  if (activeConfirmation) return activeConfirmation;

  activeConfirmation = new Promise((resolve) => {
    dialogSequence += 1;
    const titleId = `delete-dialog-title-${dialogSequence}`;
    const descId = `delete-dialog-desc-${dialogSequence}`;
    const previousFocus = document.activeElement;
    const overlay = document.createElement("div");
    overlay.className = "delete-dialog-overlay";
    overlay.dataset.deleteDialog = "true";
    overlay.innerHTML = `
      <div class="delete-dialog" role="dialog" aria-modal="true" aria-labelledby="${titleId}" aria-describedby="${descId}">
        <div class="delete-dialog-header">
          <div class="delete-dialog-icon" aria-hidden="true">
            <i data-lucide="trash-2"></i>
          </div>
          <div>
            <div id="${titleId}" class="delete-dialog-title">${escapeHtml(title)}</div>
            <p id="${descId}" class="delete-dialog-message">${escapeHtml(message)}</p>
          </div>
        </div>
        <div class="delete-dialog-impact">
          <div class="delete-dialog-impact-title">默认删除范围</div>
          <ul>
            <li>数据库记录和页面状态</li>
            <li>关联的任务、运行记录和进度记录</li>
          </ul>
        </div>
        <label class="delete-dialog-option">
          <input type="checkbox" id="delete-dialog-artifacts">
          <span>
            <strong>同时删除产物文件</strong>
            <small>会删除本地生成文件，无法从界面恢复。</small>
          </span>
        </label>
        <div class="delete-dialog-actions">
          <button class="btn btn-ghost btn-sm" id="delete-dialog-cancel">取消</button>
          <button class="btn btn-danger btn-sm" id="delete-dialog-confirm">删除记录</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    initIcons();
    const cancelButton = overlay.querySelector("#delete-dialog-cancel");
    const confirmButton = overlay.querySelector("#delete-dialog-confirm");
    const artifactCheckbox = overlay.querySelector("#delete-dialog-artifacts");
    artifactCheckbox.addEventListener("change", () => {
      confirmButton.textContent = artifactCheckbox.checked ? "删除记录和产物" : "删除记录";
    });
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
        cleanup(null);
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = [artifactCheckbox, cancelButton, confirmButton];
      const current = focusable.indexOf(document.activeElement);
      const next = event.shiftKey
        ? (current <= 0 ? focusable.length - 1 : current - 1)
        : (current === focusable.length - 1 ? 0 : current + 1);
      event.preventDefault();
      focusable[next].focus();
    };
    cancelButton.addEventListener("click", () => cleanup(null));
    confirmButton.addEventListener("click", () => {
      cleanup(artifactCheckbox.checked);
    });
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) cleanup(null);
    });
    document.addEventListener("keydown", onKeyDown);
    cancelButton.focus();
  });
  return activeConfirmation;
}
