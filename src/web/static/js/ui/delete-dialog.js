import { escapeHtml } from "./format.js";

let activeConfirmation = null;
let dialogSequence = 0;

export function confirmDeletion({ title, message }) {
  if (activeConfirmation) return activeConfirmation;

  activeConfirmation = new Promise((resolve) => {
    dialogSequence += 1;
    const titleId = `delete-dialog-title-${dialogSequence}`;
    const previousFocus = document.activeElement;
    const overlay = document.createElement("div");
    overlay.className = "delete-dialog-overlay";
    overlay.dataset.deleteDialog = "true";
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
      <div role="dialog" aria-modal="true" aria-labelledby="${titleId}" style="width: min(460px, 100%); border: 1px solid var(--line); border-radius: var(--radius-md); background: var(--paper); box-shadow: var(--shadow-lg);">
        <div style="padding: var(--space-lg); border-bottom: 1px solid var(--line);">
          <div id="${titleId}" style="font-size: var(--font-lg); font-weight: 600; color: var(--ink-800);">${escapeHtml(title)}</div>
          <p style="margin-top: var(--space-sm); color: var(--ink-600); font-size: var(--font-md);">${escapeHtml(message)}</p>
        </div>
        <label style="display: flex; align-items: center; gap: var(--space-sm); padding: var(--space-md) var(--space-lg); color: var(--ink-700);">
          <input type="checkbox" id="delete-dialog-artifacts">
          <span>同时删除产物</span>
        </label>
        <div style="display: flex; justify-content: flex-end; gap: var(--space-sm); padding: var(--space-md) var(--space-lg); border-top: 1px solid var(--line);">
          <button class="btn btn-ghost btn-sm" id="delete-dialog-cancel">Cancel</button>
          <button class="btn btn-danger btn-sm" id="delete-dialog-confirm">Delete</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    const cancelButton = overlay.querySelector("#delete-dialog-cancel");
    const confirmButton = overlay.querySelector("#delete-dialog-confirm");
    const artifactCheckbox = overlay.querySelector("#delete-dialog-artifacts");
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
