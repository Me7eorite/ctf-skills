import { escapeHtml } from "./format.js";
import { initIcons } from "./icons.js";

let activeDialog = null;
let dialogSequence = 0;

export function confirmFormDialog({
  title,
  description,
  confirmLabel,
  icon = "file-pen-line",
  fields = [],
  confirmTone = "primary",
}) {
  if (activeDialog) return activeDialog;

  activeDialog = new Promise((resolve) => {
    dialogSequence += 1;
    const titleId = `form-dialog-title-${dialogSequence}`;
    const descId = `form-dialog-desc-${dialogSequence}`;
    const errorId = `form-dialog-error-${dialogSequence}`;
    const previousFocus = document.activeElement;
    const overlay = document.createElement("div");
    overlay.className = "form-dialog-overlay";
    overlay.dataset.formDialog = "true";
    overlay.style.cssText = [
      "position: fixed",
      "inset: 0",
      "z-index: 1000",
      "display: grid",
      "place-items: center",
      "background: rgba(15, 23, 42, 0.36)",
      "padding: 20px",
    ].join(";");

    const fieldMarkup = fields.map((field) => {
      const label = escapeHtml(field.label || "");
      const value = escapeHtml(field.value || "");
      const helper = field.helper ? `<small class="form-dialog-help">${escapeHtml(field.helper)}</small>` : "";
      if (field.type === "textarea") {
        return `
          <label class="form-dialog-field">
            <span>${label}${field.required ? " <em>*</em>" : ""}</span>
            <textarea id="${escapeHtml(field.id)}" rows="${field.rows || 4}" ${field.required ? "required" : ""}>${value}</textarea>
            ${helper}
          </label>
        `;
      }
      if (field.type === "select") {
        return `
          <label class="form-dialog-field">
            <span>${label}${field.required ? " <em>*</em>" : ""}</span>
            <select id="${escapeHtml(field.id)}" ${field.required ? "required" : ""}>
              ${(field.options || []).map((option) => {
                const optionValue = typeof option === "string" ? option : option.value;
                const optionLabel = typeof option === "string" ? option : option.label;
                const selected = String(optionValue) === String(field.value) ? " selected" : "";
                return `<option value="${escapeHtml(optionValue)}"${selected}>${escapeHtml(optionLabel)}</option>`;
              }).join("")}
            </select>
            ${helper}
          </label>
        `;
      }
      return `
        <label class="form-dialog-field">
          <span>${label}${field.required ? " <em>*</em>" : ""}</span>
          <input id="${escapeHtml(field.id)}" type="${escapeHtml(field.type || "text")}" value="${value}" ${field.placeholder ? `placeholder="${escapeHtml(field.placeholder)}"` : ""} ${field.required ? "required" : ""}>
          ${helper}
        </label>
      `;
    }).join("");

    overlay.innerHTML = `
      <div role="dialog" aria-modal="true" aria-labelledby="${titleId}" aria-describedby="${descId}" style="width: min(640px, 100%); border: 1px solid var(--line); border-radius: var(--radius-md); background: var(--paper); box-shadow: var(--shadow-lg);">
        <div style="padding: var(--space-lg); border-bottom: 1px solid var(--line);">
          <div style="display:flex; gap:12px; align-items:flex-start;">
            <div aria-hidden="true" style="width: 32px; height: 32px; border-radius: 8px; display:grid; place-items:center; background: var(--accent-10); color: var(--accent-700); flex: 0 0 auto;">
              <i data-lucide="${escapeHtml(icon)}"></i>
            </div>
            <div style="min-width:0;">
              <div id="${titleId}" style="font-size: var(--font-lg); font-weight: 600; color: var(--ink-800);">${escapeHtml(title)}</div>
              <p id="${descId}" style="margin-top: var(--space-sm); color: var(--ink-600); font-size: var(--font-md); overflow-wrap:anywhere;">${escapeHtml(description || "")}</p>
            </div>
          </div>
        </div>
        <div style="padding: var(--space-md) var(--space-lg); display:grid; gap: var(--space-md);">
          ${fieldMarkup}
          <div id="${errorId}" style="display:none; color: var(--accent-red); font-size: var(--font-sm);"></div>
        </div>
        <div style="display: flex; justify-content: flex-end; gap: var(--space-sm); padding: var(--space-md) var(--space-lg); border-top: 1px solid var(--line);">
          <button class="btn btn-ghost btn-sm" id="form-dialog-cancel">取消</button>
          <button class="btn btn-${escapeHtml(confirmTone)} btn-sm" id="form-dialog-confirm">${escapeHtml(confirmLabel)}</button>
        </div>
      </div>
    `;

    document.body.appendChild(overlay);
    initIcons();

    const cancelButton = overlay.querySelector("#form-dialog-cancel");
    const confirmButton = overlay.querySelector("#form-dialog-confirm");
    const errorBox = overlay.querySelector(`#${CSS.escape(errorId)}`);
    const fieldMap = new Map(fields.map((field) => [field.id, field]));

    const cleanup = (value) => {
      document.removeEventListener("keydown", onKeyDown);
      overlay.remove();
      activeDialog = null;
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
      const focusable = [
        ...overlay.querySelectorAll("input, textarea, select, button"),
      ].filter((item) => !item.disabled);
      const current = focusable.indexOf(document.activeElement);
      const next = event.shiftKey
        ? (current <= 0 ? focusable.length - 1 : current - 1)
        : (current === focusable.length - 1 ? 0 : current + 1);
      event.preventDefault();
      focusable[next]?.focus();
    };

    const showError = (message) => {
      errorBox.textContent = message;
      errorBox.style.display = "block";
    };

    cancelButton.addEventListener("click", () => cleanup(null));
    confirmButton.addEventListener("click", () => {
      const result = {};
      for (const field of fields) {
        const element = overlay.querySelector(`#${CSS.escape(field.id)}`);
        const value = element?.value?.trim?.() ?? "";
        if (field.required && !value) {
          showError(`${field.label || field.id} is required`);
          element?.focus?.();
          return;
        }
        result[field.id] = value;
      }
      cleanup(result);
    });
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) cleanup(null);
    });
    document.addEventListener("keydown", onKeyDown);
    const firstField = fields.length ? overlay.querySelector(`#${CSS.escape(fields[0].id)}`) : cancelButton;
    firstField?.focus?.();
  });

  return activeDialog;
}
