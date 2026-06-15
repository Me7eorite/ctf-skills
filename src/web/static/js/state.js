export const appState = {
  data: null,
  view: "overview",
  category: "all",
  search: "",
  editingSeedId: null,
  timer: null,
};

export function isActive(data) {
  return Boolean(
    data?.process?.running
    || data?.progress?.snapshots?.some((item) => item.status === "running")
  );
}

export function scheduleRefresh(reload) {
  clearTimeout(appState.timer);
  appState.timer = setTimeout(reload, isActive(appState.data) ? 2000 : 8000);
}
