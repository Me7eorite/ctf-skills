export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(path, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.message || payload.detail || "Request failed");
  }
  return payload as T;
}

export async function apiSend<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, init);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.message || payload.detail || "Request failed");
  }
  return payload as T;
}

export interface ApiPostResult<T = unknown> {
  ok: boolean;
  message?: string;
  data?: T;
}

export async function apiPost<T = unknown>(
  path: string,
  body?: unknown,
): Promise<ApiPostResult<T>> {
  try {
    const init: RequestInit = { method: "POST", cache: "no-store" };
    if (body !== undefined) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(body);
    }
    const response = await fetch(path, init);
    let payload: Record<string, unknown> = {};
    try {
      payload = (await response.json()) as Record<string, unknown>;
    } catch {
      payload = {};
    }
    const ok = response.ok && payload.ok !== false;
    const messageField = payload.message ?? payload.detail;
    const message = typeof messageField === "string" ? messageField : undefined;
    return { ok, message, data: payload as T };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : "网络错误",
    };
  }
}
