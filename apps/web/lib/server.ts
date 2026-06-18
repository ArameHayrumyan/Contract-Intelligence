/**
 * Server-only helpers: backend routing and the access-gate cookie.
 *
 * This module must never be imported by a client component — it reads secrets
 * (the backend API key, the shared access code) from server env. The frontend
 * talks to the backend exclusively through the same-origin proxy route handlers
 * (`app/api/**`), which attach the API key here, so the key never reaches the
 * browser.
 */

import "server-only";

export { ACCESS_COOKIE } from "@/lib/constants";

/** Backend (FastAPI) base URL, e.g. http://api:8000. */
export function backendBaseUrl(): string {
  return process.env.API_BASE_URL ?? "http://localhost:8000";
}

/** Backend API key used for the stub auth layer (server-side only). */
export function backendApiKey(): string {
  return process.env.API_KEY ?? "demo-key-tenant-acme";
}

/** Shared access code required to enter the app (server-side only). */
export function accessCode(): string {
  return process.env.ACCESS_CODE ?? "change-me-locally";
}

/**
 * The opaque value stored in the access cookie once the gate is passed.
 *
 * We store a non-reversible-ish token rather than the code itself so the raw
 * code is not sitting in the browser. For the demo this is a simple derived
 * value; at scale this becomes a signed session (Constraint #5 / SCALING_PATH).
 */
export function accessCookieValue(): string {
  const code = accessCode();
  // Lightweight derivation; replace with a signed JWT/session at scale.
  return Buffer.from(`granted:${code}`).toString("base64url");
}

/** Whether a presented cookie value grants access. */
export function isAccessGranted(cookieValue: string | undefined): boolean {
  return Boolean(cookieValue) && cookieValue === accessCookieValue();
}
