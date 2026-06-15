let timer;

export function showToast(message, error = false) {
  const toast = document.querySelector("#toast");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.remove("hidden");
  toast.classList.toggle("error", Boolean(error));
  clearTimeout(timer);
  timer = setTimeout(() => toast.classList.add("hidden"), 2400);
}
