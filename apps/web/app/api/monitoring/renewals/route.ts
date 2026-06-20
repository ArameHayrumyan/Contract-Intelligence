/** Proxy: GET /api/monitoring/renewals -> backend GET /monitoring/renewals */

import { NextRequest } from "next/server";

import { proxy } from "@/lib/proxy";

export async function GET(request: NextRequest) {
  return proxy({
    method: "GET",
    path: `/monitoring/renewals${request.nextUrl.search}`,
  });
}
