/** Proxy (binary): POST /api/dashboard/contracts/bulk/export -> zip of PDFs */

import { NextRequest } from "next/server";

import { proxyBinary } from "@/lib/proxy";

export async function POST(request: NextRequest) {
  const body = await request.text();
  return proxyBinary({
    method: "POST",
    path: "/dashboard/contracts/bulk/export",
    body,
    headers: { "Content-Type": "application/json" },
  });
}
