// Page routing: send signed-out visitors to the sign-in page. The API still authorises every
// request; this only avoids rendering app pages that would fail.
import { NextResponse, type NextRequest } from "next/server";

const PUBLIC = ["/login", "/register", "/reset-password"];

export function proxy(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  const signedIn = req.cookies.has("q_s");
  if (PUBLIC.some((p) => pathname.startsWith(p))) {
    return signedIn && pathname !== "/reset-password"
      ? NextResponse.redirect(new URL("/", req.url)) : NextResponse.next();
  }
  if (!signedIn) {
    const url = new URL("/login", req.url);
    url.searchParams.set("next", pathname + search);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!bff|_next|favicon.ico|robots.txt).*)"],
};
