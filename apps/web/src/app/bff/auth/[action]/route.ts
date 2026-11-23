// Sign-in flows that create or end a session. Tokens go into httpOnly cookies and are never
// returned to browser JavaScript.
import { NextResponse } from "next/server";

import {
  API, clearTokens, clientHeaders, csrfOk, forbidden, readTokens, setTokens, type Tokens,
} from "@/lib/server/session";

const PATHS: Record<string, string> = {
  login: "/api/v1/auth/login",
  register: "/api/v1/auth/register",
};

export async function POST(req: Request, ctx: { params: Promise<{ action: string }> }) {
  if (!csrfOk(req)) return forbidden();
  const { action } = await ctx.params;

  if (action === "logout") {
    const { access } = await readTokens();
    if (access) {
      await fetch(`${API}/api/v1/auth/logout`, {
        method: "POST", headers: { authorization: `Bearer ${access}` }, cache: "no-store",
      }).catch(() => undefined);
    }
    const res = new NextResponse(null, { status: 204 });
    clearTokens(res);
    return res;
  }

  const path = PATHS[action];
  if (!path) return NextResponse.json({ error: { code: "not_found", message: "Unknown action" } },
                                      { status: 404 });
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", ...clientHeaders(req) },
    body: await req.text(),
    cache: "no-store",
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) return NextResponse.json(body, { status: r.status });
  const res = NextResponse.json({ ok: true }, { status: r.status });
  setTokens(res, body as Tokens);
  return res;
}
