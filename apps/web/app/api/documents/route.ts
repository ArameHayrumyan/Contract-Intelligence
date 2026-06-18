/**
 * Proxy: POST /api/documents  →  backend POST /documents (multipart upload).
 */

import { NextRequest } from "next/server";

import { proxy } from "@/lib/proxy";

export async function POST(request: NextRequest) {
  // Forward the multipart body verbatim; fetch sets the multipart boundary
  // header automatically when given FormData.
  const form = await request.formData();
  return proxy({ method: "POST", path: "/documents", body: form });
}
