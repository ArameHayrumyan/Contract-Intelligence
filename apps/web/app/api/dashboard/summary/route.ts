/** Proxy: GET /api/dashboard/summary -> backend GET /dashboard/summary */

import { proxy } from "@/lib/proxy";

export async function GET() {
  return proxy({ method: "GET", path: "/dashboard/summary" });
}
