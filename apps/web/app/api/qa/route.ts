/**
 * Proxy: POST /api/qa  →  backend POST /qa (cross-document RAG-fusion QA).
 */

import { NextRequest } from "next/server";

import { proxy } from "@/lib/proxy";

export async function POST(request: NextRequest) {
  const body = await request.text();
  return proxy({
    method: "POST",
    path: "/qa",
    body,
    headers: { "Content-Type": "application/json" },
  });
}
