"use client";
// Browser-side API access: everything goes through the same-origin /bff proxy.

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string,
              public details: Record<string, unknown> = {}) {
    super(message);
  }
}

export type Query = Record<string, string | number | boolean | null | undefined | string[]>;

export function qs(query?: Query): string {
  if (!query) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === null || v === undefined || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, x));
    else p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

async function parse(r: Response): Promise<unknown> {
  if (r.status === 204) return null;
  const ct = r.headers.get("content-type") ?? "";
  return ct.includes("json") ? r.json() : r.text();
}

export async function request<T>(base: string, path: string,
                                 opts: { method?: string; body?: unknown; query?: Query;
                                         form?: FormData; anon?: boolean } = {}): Promise<T> {
  const method = opts.method ?? (opts.body !== undefined || opts.form ? "POST" : "GET");
  const headers: Record<string, string> = { "x-quantera-csrf": "1" };
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["content-type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  let r: Response;
  try {
    r = await fetch(`${base}/${path.replace(/^\//, "")}${qs(opts.query)}`,
                    { method, headers, body, cache: "no-store" });
  } catch {
    throw new ApiError(0, "offline", "Cannot reach the server. Check your connection.");
  }
  const data = await parse(r);
  if (!r.ok) {
    const e = (data as { error?: { code?: string; message?: string; details?: Record<string, unknown> } })
      ?.error;
    if (r.status === 401 && base === "/bff/api" && !opts.anon && typeof window !== "undefined"
        && e?.code !== "mfa_required") {
      const next = encodeURIComponent(location.pathname + location.search);
      location.assign(`/login?next=${next}`);
    }
    throw new ApiError(r.status, e?.code ?? "error", e?.message ?? `Request failed (${r.status})`,
                       e?.details ?? {});
  }
  return data as T;
}

export const api = <T,>(path: string, opts?: Parameters<typeof request>[2]) =>
  request<T>("/bff/api", path, opts);

export const auth = <T,>(action: string, body?: unknown) =>
  request<T>("/bff/auth", action, { method: "POST", body: body ?? {} });

export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const errs = (e.details?.errors as { loc?: unknown[]; msg?: string }[] | undefined) ?? [];
    if (errs.length) {
      return errs.map((x) => `${(x.loc ?? []).slice(1).join(".") || "request"}: ${x.msg}`)
        .join("; ");
    }
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}
