/**
 * Proxy: PATCH /api/dashboard/contracts/:id/status
 *        -> backend PATCH /dashboard/contracts/:id/status
 */

import { NextRequest } from "next/server";

import { proxy } from "@/lib/proxy";

export async function PATCH(
  request: NextRequest,
  { params }: { params: { documentId: string } },
) {
  const body = await request.text();
  return proxy({
    method: "PATCH",
    path: `/dashboard/contracts/${encodeURIComponent(params.documentId)}/status`,
    body,
    headers: { "Content-Type": "application/json" },
  });
}
