/** Proxy: GET /api/dashboard/contracts -> backend GET /dashboard/contracts */

import { NextRequest } from "next/server";

import { proxy } from "@/lib/proxy";

export async function GET(request: NextRequest) {
  return proxy({
    method: "GET",
    path: `/dashboard/contracts${request.nextUrl.search}`,
  });
}
