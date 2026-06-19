/**
 * Proxy: /api/standards
 *   POST  -> backend POST /standards (multipart: name, version, PDF)
 *   GET   -> backend GET  /standards (grouped list)
 */

import { NextRequest } from "next/server";

import { proxy } from "@/lib/proxy";

export async function POST(request: NextRequest) {
  const form = await request.formData();
  return proxy({ method: "POST", path: "/standards", body: form });
}

export async function GET() {
  return proxy({ method: "GET", path: "/standards" });
}
