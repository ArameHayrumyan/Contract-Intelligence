/**
 * Proxy: /api/documents/:id/annotations
 *   GET  -> backend GET  /documents/:id/annotations
 *   POST -> backend POST /documents/:id/annotations
 */

import { NextRequest } from "next/server";

import { proxy } from "@/lib/proxy";

export async function GET(
  request: NextRequest,
  { params }: { params: { documentId: string } },
) {
  const id = encodeURIComponent(params.documentId);
  return proxy({
    method: "GET",
    path: `/documents/${id}/annotations${request.nextUrl.search}`,
  });
}

export async function POST(
  request: NextRequest,
  { params }: { params: { documentId: string } },
) {
  const body = await request.text();
  return proxy({
    method: "POST",
    path: `/documents/${encodeURIComponent(params.documentId)}/annotations`,
    body,
    headers: { "Content-Type": "application/json" },
  });
}
