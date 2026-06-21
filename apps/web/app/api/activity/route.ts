/** Proxy: GET /api/activity -> backend GET /activity */

import { NextRequest } from "next/server";

import { proxy } from "@/lib/proxy";

export async function GET(request: NextRequest) {
  return proxy({ method: "GET", path: `/activity${request.nextUrl.search}` });
}
