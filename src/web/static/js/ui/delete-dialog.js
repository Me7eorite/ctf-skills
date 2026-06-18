import { escapeHtml } from "./format.js";

export function confirmDeletion({ title, message }) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "delete-dialog-overlay";
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
      <div role="dialog" aria-modal="true" aria-labelledby="delete-dialog-title" style="width: min(460px, 100%); border: 1px solid var(--line); border-radius: var(--radius-md); background: var(--paper); box-shadow: var(--shadow-lg);">
        <div style="padding: var(--space-lg); border-bottom: 1px solid var(--line);">
          <div id="delete-dialog-title" style="font-size: var(--font-lg); font-weight: 600; color: var(--ink-800);">${escapeHtml(title)}</div>
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
    const cleanup = (value) => {
      overlay.remove();
      resolve(value);
    };
    overlay.querySelector("#delete-dialog-cancel").addEventListener("click", () => cleanup(null));
    overlay.querySelector("#delete-dialog-confirm").addEventListener("click", () => {
      cleanup(overlay.querySelector("#delete-dialog-artifacts").checked);
    });
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) cleanup(null);
    });
  });
}
