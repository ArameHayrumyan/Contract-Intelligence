/**
 * Proxy (binary): GET /api/documents/:id/export/pdf
 *                 -> backend GET /documents/:id/export/pdf
 */

import { NextRequest } from "next/server";

import { proxyBinary } from "@/lib/proxy";

export async function GET(
  _request: NextRequest,
  { params }: { params: { documentId: string } },
) {
  return proxyBinary({
    method: "GET",
    path: `/documents/${encodeURIComponent(params.documentId)}/export/pdf`,
  });
}
