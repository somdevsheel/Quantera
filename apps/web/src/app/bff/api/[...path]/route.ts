// Same-origin proxy to the platform API. Adds the access token from the httpOnly cookie,
// refreshes it once on 401, and keeps token-bearing responses away from the browser.
import { NextResponse } from "next/server";

import {
  API, clearTokens, clientHeaders, csrfOk, forbidden, readTokens, refreshTokens, setTokens,
  type Tokens,
} from "@/lib/server/session";

type Ctx = { params: Promise<{ path: string[] }> };

const PASS_HEADERS = ["content-type", "content-disposition", "location", "cache-control"];
const MAX_BODY = 5 * 1024 * 1024; // matches the API's upload limit

async function forward(req: Request, path: string, body: ArrayBuffer | undefined,
                       access: string | undefined): Promise<Response> {
  const url = new URL(req.url);
  const headers: Record<string, string> = { ...clientHeaders(req) };
  const ct = req.headers.get("content-type");
  if (ct) headers["content-type"] = ct;
  if (access) headers.authorization = `Bearer ${access}`;
  return fetch(`${API}/api/v1/${path}${url.search}`, {
    method: req.method, headers, body, cache: "no-store", redirect: "manual",
  });
}

async function handle(req: Request, ctx: Ctx): Promise<Response> {
  if (!csrfOk(req)) return forbidden();
  const { path: parts } = await ctx.params;
  if (parts.some((p) => p === ".." || p.includes("/"))) return forbidden();
  const path = parts.map(encodeURIComponent).join("/");
  let body: ArrayBuffer | undefined;
  if (!["GET", "HEAD"].includes(req.method)) {
    body = await req.arrayBuffer();
    if (body.byteLength > MAX_BODY) {
      return NextResponse.json({ error: { code: "too_large", message: "Upload too large" } },
                               { status: 413 });
    }
  }
  const { access, refresh } = await readTokens();
  let r = await forward(req, path, body, access);
  let renewed: Tokens | null = null;
  if (r.status === 401 && refresh) {
    renewed = await refreshTokens(refresh);
    if (renewed) r = await forward(req, path, body, renewed.access_token);
  }

  // Responses that carry tokens (switching workspace) are turned into cookies.
  if (path === "auth/switch-workspace" && r.ok) {
    const res = NextResponse.json({ ok: true });
    setTokens(res, (await r.json()) as Tokens);
    return res;
  }

  const headers = new Headers();
  for (const h of PASS_HEADERS) {
    const v = r.headers.get(h);
    if (v) headers.set(h, v);
  }
  const res = new NextResponse(r.status === 204 ? null : r.body, { status: r.status, headers });
  if (renewed) setTokens(res, renewed);
  // Signed out on the server (expired, revoked, password changed): drop local cookies.
  if (r.status === 401 || (path === "auth/password" && r.ok)) clearTokens(res);
  return res;
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
