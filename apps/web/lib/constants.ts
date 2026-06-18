/**
 * Runtime-neutral constants safe to import from both Edge middleware and
 * server modules (no Node APIs, no `server-only` marker).
 */

/** Name of the session cookie set after a successful access-code entry. */
export const ACCESS_COOKIE = "sci_access";
