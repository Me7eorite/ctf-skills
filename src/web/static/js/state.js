export const appState = {
  data: null,
  view: "overview",
  category: "all",
  search: "",
  editingSeedId: null,
  timer: null,
  buildAttempts: {
    list: null,
    detail: null,
    detailId: null,
    filters: {
      status: "",
      worker: "",
      category: "",
      design_task_id: "",
      generation_request_id: "",
    },
    selection: new Set(),
    flags: {},
    poll: { timer: null, loading: false },
  },
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
