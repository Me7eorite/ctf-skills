export async function api(path, options = {}) {
  const response = await fetch(path, options);
  let payload = {};
  try { payload = await response.json(); } catch { /* ignore body parse errors */ }
  if (!response.ok) {
    throw new Error(payload.message || payload.detail || payload.error || `请求失败 (${response.status})`);
  }
  return payload;
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
