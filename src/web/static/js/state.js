export const appState = {
  data: null,
  view: "overview",
  category: "all",
  search: "",
  timer: null,
  stateLoading: false,
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
    filterDraft: {
      status: "",
      worker: "",
      category: "",
      design_task_id: "",
      generation_request_id: "",
    },
    selection: new Set(),
    laneCount: 4,
    lanePools: null,
    flags: {},
    poll: { timer: null, loading: false },
  },
};

const ACTIVE_REFRESH_MS = 5000;
const SETTLED_REFRESH_MS = 20000;
const HIDDEN_REFRESH_MS = 60000;

export function isActive(data) {
  return Boolean(
    data?.process?.running
    || data?.progress?.snapshots?.some((item) => item.status === "running")
  );
}

export function scheduleRefresh(reload) {
  clearTimeout(appState.timer);
  const hidden = typeof document !== "undefined" && document.hidden;
  const delay = hidden
    ? HIDDEN_REFRESH_MS
    : (isActive(appState.data) ? ACTIVE_REFRESH_MS : SETTLED_REFRESH_MS);
  appState.timer = setTimeout(reload, delay);
}
