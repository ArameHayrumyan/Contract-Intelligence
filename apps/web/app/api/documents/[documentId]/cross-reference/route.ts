/**
 * Proxy: POST /api/documents/:id/cross-reference
 *        -> backend POST /documents/:id/cross-reference
 */

import { NextRequest } from "next/server";

import { proxy } from "@/lib/proxy";

export async function POST(
  request: NextRequest,
  { params }: { params: { documentId: string } },
) {
  const body = await request.text();
  return proxy({
    method: "POST",
    path: `/documents/${encodeURIComponent(params.documentId)}/cross-reference`,
    body,
    headers: { "Content-Type": "application/json" },
  });
}
