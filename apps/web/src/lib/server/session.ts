// Server-only helpers: token cookies and calls to the platform API.
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

export const API = (process.env.QUANTERA_API_URL ?? "http://127.0.0.1:8002").replace(/\/$/, "");
export const CSRF_HEADER = "x-quantera-csrf";

const AT = "q_at"; // access token  (short-lived)
const RT = "q_rt"; // refresh token (rotating), only ever sent to /bff
const FLAG = "q_s"; // "signed in" hint for page routing; carries no secret

export type Tokens = {
  access_token: string;
  access_expires_at: string;
  refresh_token: string;
  refresh_expires_at: string;
};

const secure = process.env.NODE_ENV === "production";

export function setTokens(res: NextResponse, t: Tokens): void {
  const base = { httpOnly: true, secure, sameSite: "strict" as const };
  res.cookies.set(AT, t.access_token, { ...base, path: "/bff", expires: new Date(t.access_expires_at) });
  res.cookies.set(RT, t.refresh_token, { ...base, path: "/bff", expires: new Date(t.refresh_expires_at) });
  res.cookies.set(FLAG, "1", { ...base, path: "/", expires: new Date(t.refresh_expires_at) });
}

export function clearTokens(res: NextResponse): void {
  for (const [name, path] of [[AT, "/bff"], [RT, "/bff"], [FLAG, "/"]] as const) {
    res.cookies.set(name, "", { httpOnly: true, secure, sameSite: "strict", path, maxAge: 0 });
  }
}

export async function readTokens(): Promise<{ access?: string; refresh?: string }> {
  const jar = await cookies();
  return { access: jar.get(AT)?.value, refresh: jar.get(RT)?.value };
}

export async function refreshTokens(refresh: string): Promise<Tokens | null> {
  const r = await fetch(`${API}/api/v1/auth/refresh`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
    cache: "no-store",
  });
  return r.ok ? ((await r.json()) as Tokens) : null;
}

/** Mutating requests must carry the custom header (a cross-site form cannot set it). */
export function csrfOk(req: Request): boolean {
  return req.method === "GET" || req.method === "HEAD" || req.headers.get(CSRF_HEADER) === "1";
}

export function forbidden(): NextResponse {
  return NextResponse.json(
    { error: { code: "csrf", message: "Missing request header" } }, { status: 403 });
}

export function clientHeaders(req: Request): Record<string, string> {
  const h: Record<string, string> = {};
  const ua = req.headers.get("user-agent");
  if (ua) h["user-agent"] = ua;
  const fwd = req.headers.get("x-forwarded-for");
  if (fwd) h["x-forwarded-for"] = fwd;
  return h;
}
