/** Proxy (binary): GET /api/portfolio/export/pdf -> backend GET /portfolio/export/pdf */

import { proxyBinary } from "@/lib/proxy";

export async function GET() {
  return proxyBinary({ method: "GET", path: "/portfolio/export/pdf" });
}
