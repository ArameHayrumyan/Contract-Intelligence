/** Proxy: POST /api/dashboard/contracts/bulk/status -> backend bulk status */

import { NextRequest } from "next/server";

import { proxy } from "@/lib/proxy";

export async function POST(request: NextRequest) {
  const body = await request.text();
  return proxy({
    method: "POST",
    path: "/dashboard/contracts/bulk/status",
    body,
    headers: { "Content-Type": "application/json" },
  });
}
