/** Thin fetch wrapper. Errors carry a plain-language message ready to show in a toast. */
export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

function describe(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc: ["body","byte_limit"], msg: "..."}]
    return detail.map((d: { loc?: string[]; msg?: string }) => `${(d.loc ?? []).slice(1).join(".") || "value"}: ${d.msg}`).join("; ");
  }
  return `The server returned an error (${status}).`;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Cannot reach the LoRaPedia server. Is it running?", 0);
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(describe(data, res.status), res.status);
  return data as T;
}

export const api = {
  get: <T,>(path: string) => request<T>("GET", path),
  post: <T,>(path: string, body: unknown = {}) => request<T>("POST", path, body),
  put: <T,>(path: string, body: unknown) => request<T>("PUT", path, body),
  del: <T,>(path: string) => request<T>("DELETE", path),
};

export const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));
