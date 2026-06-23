const inflightGetRequests = new Map();

export async function api(path, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const canDedupe = method === "GET" && !options.signal;
  const dedupeKey = canDedupe ? String(path) : null;
  if (dedupeKey && inflightGetRequests.has(dedupeKey)) {
    return inflightGetRequests.get(dedupeKey);
  }

  const request = fetch(path, options)
    .then(async (response) => {
      let payload = {};
      try { payload = await response.json(); } catch { /* ignore body parse errors */ }
      if (!response.ok) {
        const detail = payload.message || payload.detail || payload.error;
        const message = typeof detail === "object" && detail !== null
          ? detail.code || JSON.stringify(detail)
          : detail;
        throw new Error(message || `请求失败 (${response.status})`);
      }
      return payload;
    })
    .finally(() => {
      if (dedupeKey) inflightGetRequests.delete(dedupeKey);
    });

  if (dedupeKey) inflightGetRequests.set(dedupeKey, request);
  return request;
}

export async function postJson(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

export async function del(path) {
  return api(path, { method: "DELETE" });
}
