/**
 * Server-side proxy helper used by the `app/api/**` route handlers.
 *
 * Forwards a browser request to the FastAPI backend, attaching the server-held
 * API key and enforcing the access-cookie gate. Centralising this keeps every
 * proxy route a one-liner and ensures the credential handling is consistent.
 */

import "server-only";

import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import {
  ACCESS_COOKIE,
  backendApiKey,
  backendBaseUrl,
  isAccessGranted,
} from "@/lib/server";

interface ProxyOptions {
  method: string;
  /** Backend path beginning with `/`, e.g. `/documents`. */
  path: string;
  /** Raw body to forward (string for JSON, FormData for uploads). */
  body?: BodyInit;
  /** Extra headers to merge (e.g. Content-Type for JSON). */
  headers?: Record<string, string>;
}

/**
 * Forward a request to the backend with auth + gate enforcement.
 *
 * @returns The backend response re-wrapped as a `NextResponse`, or a 401 if the
 *   access gate is not satisfied.
 */
export async function proxy(options: ProxyOptions): Promise<NextResponse> {
  const cookieStore = cookies();
  const granted = isAccessGranted(cookieStore.get(ACCESS_COOKIE)?.value);
  if (!granted) {
    return NextResponse.json(
      { detail: "Access code required." },
      { status: 401 },
    );
  }

  const url = `${backendBaseUrl()}${options.path}`;
  let res: Response;
  try {
    res = await fetch(url, {
      method: options.method,
      headers: { "X-API-Key": backendApiKey(), ...(options.headers ?? {}) },
      body: options.body,
      cache: "no-store",
    });
  } catch {
    return NextResponse.json(
      { detail: "Backend unreachable." },
      { status: 502 },
    );
  }

  const text = await res.text();
  return new NextResponse(text, {
    status: res.status,
    headers: {
      "Content-Type": res.headers.get("Content-Type") ?? "application/json",
    },
  });
}

/**
 * Binary variant of {@link proxy} for downloads (PDFs).
 *
 * The text-based `proxy` would corrupt binary bodies, so this forwards the raw
 * bytes and preserves `Content-Type` / `Content-Disposition`.
 *
 * @returns The backend response as binary, or a 401/502 JSON error.
 */
export async function proxyBinary(options: ProxyOptions): Promise<NextResponse> {
  const cookieStore = cookies();
  if (!isAccessGranted(cookieStore.get(ACCESS_COOKIE)?.value)) {
    return NextResponse.json({ detail: "Access code required." }, { status: 401 });
  }

  let res: Response;
  try {
    res = await fetch(`${backendBaseUrl()}${options.path}`, {
      method: options.method,
      headers: { "X-API-Key": backendApiKey(), ...(options.headers ?? {}) },
      body: options.body,
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ detail: "Backend unreachable." }, { status: 502 });
  }

  if (!res.ok) {
    // Surface the JSON error body (e.g. 404) without binary handling.
    return new NextResponse(await res.text(), {
      status: res.status,
      headers: { "Content-Type": res.headers.get("Content-Type") ?? "application/json" },
    });
  }

  const buffer = await res.arrayBuffer();
  const headers: Record<string, string> = {
    "Content-Type": res.headers.get("Content-Type") ?? "application/pdf",
  };
  const disposition = res.headers.get("Content-Disposition");
  if (disposition) headers["Content-Disposition"] = disposition;
  return new NextResponse(buffer, { status: res.status, headers });
}
