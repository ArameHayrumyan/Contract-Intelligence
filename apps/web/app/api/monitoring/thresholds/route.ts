/**
 * Proxy: /api/monitoring/thresholds
 *   GET   -> backend GET   /monitoring/thresholds
 *   PATCH -> backend PATCH /monitoring/thresholds
 */

import { NextRequest } from "next/server";

import { proxy } from "@/lib/proxy";

export async function GET() {
  return proxy({ method: "GET", path: "/monitoring/thresholds" });
}

export async function PATCH(request: NextRequest) {
  const body = await request.text();
  return proxy({
    method: "PATCH",
    path: "/monitoring/thresholds",
    body,
    headers: { "Content-Type": "application/json" },
  });
}
