/**
 * Proxy: /api/documents/:id/annotations/:annotationId
 *   PATCH  -> backend PATCH  (edit)
 *   DELETE -> backend DELETE (soft delete)
 */

import { NextRequest } from "next/server";

import { proxy } from "@/lib/proxy";

function path(documentId: string, annotationId: string): string {
  return `/documents/${encodeURIComponent(documentId)}/annotations/${encodeURIComponent(annotationId)}`;
}

export async function PATCH(
  request: NextRequest,
  { params }: { params: { documentId: string; annotationId: string } },
) {
  const body = await request.text();
  return proxy({
    method: "PATCH",
    path: path(params.documentId, params.annotationId),
    body,
    headers: { "Content-Type": "application/json" },
  });
}

export async function DELETE(
  _request: NextRequest,
  { params }: { params: { documentId: string; annotationId: string } },
) {
  return proxy({
    method: "DELETE",
    path: path(params.documentId, params.annotationId),
  });
}
