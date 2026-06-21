/** Proxy: GET /api/documents/:id/activity -> backend GET /documents/:id/activity */

import { NextRequest } from "next/server";

import { proxy } from "@/lib/proxy";

export async function GET(
  _request: NextRequest,
  { params }: { params: { documentId: string } },
) {
  return proxy({
    method: "GET",
    path: `/documents/${encodeURIComponent(params.documentId)}/activity`,
  });
}
