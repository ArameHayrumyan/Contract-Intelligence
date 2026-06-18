/**
 * Application-level access gate (Section 6).
 *
 * Redirects unauthenticated visitors to the branded `/access` page unless they
 * carry a valid session cookie. The access-code check itself lives in
 * `app/api/access`; this middleware only checks for cookie *presence* (Edge
 * runtime cannot use the Node `Buffer`-based comparison), and the proxy routes
 * re-verify the cookie value server-side before forwarding to the backend.
 */

import { NextRequest, NextResponse } from "next/server";

import { ACCESS_COOKIE } from "@/lib/constants";

/** Paths reachable without a session (the gate page and its API). */
const PUBLIC_PATHS = new Set<string>(["/access"]);

export function middleware(request: NextRequest): NextResponse {
  const { pathname } = request.nextUrl;

  // Always allow the access page and the access API.
  if (PUBLIC_PATHS.has(pathname) || pathname.startsWith("/api/access")) {
    return NextResponse.next();
  }

  const hasCookie = Boolean(request.cookies.get(ACCESS_COOKIE)?.value);
  if (!hasCookie) {
    const url = request.nextUrl.clone();
    url.pathname = "/access";
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  // Run on everything except Next internals and static assets.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
