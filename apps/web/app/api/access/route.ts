/**
 * Access-gate endpoint: validates the shared access code and sets the session
 * cookie (Section 6, application-level gate). The code is checked server-side;
 * only an opaque derived token is ever stored in the browser.
 */

import { NextRequest, NextResponse } from "next/server";

import { ACCESS_COOKIE, accessCode, accessCookieValue } from "@/lib/server";

export async function POST(request: NextRequest) {
  let submitted = "";
  try {
    const body = await request.json();
    submitted = typeof body?.code === "string" ? body.code : "";
  } catch {
    return NextResponse.json({ detail: "Invalid request body." }, { status: 400 });
  }

  if (submitted !== accessCode()) {
    return NextResponse.json({ detail: "Incorrect access code." }, { status: 401 });
  }

  const response = NextResponse.json({ ok: true });
  response.cookies.set(ACCESS_COOKIE, accessCookieValue(), {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 8, // 8 hours
  });
  return response;
}
