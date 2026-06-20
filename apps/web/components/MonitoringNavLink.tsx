"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { getRenewals } from "@/lib/api-client";

/** Monitoring nav link with an amber dot when contracts are at renewal risk. */
export function MonitoringNavLink() {
  const [atRisk, setAtRisk] = useState(0);

  useEffect(() => {
    getRenewals([30, 60, 90], false)
      .then((r) => setAtRisk(r.total_at_risk))
      .catch(() => setAtRisk(0));
  }, []);

  return (
    <Link href="/monitoring">
      Monitoring
      {atRisk > 0 ? <span className="nav-dot" title={`${atRisk} at risk`} /> : null}
    </Link>
  );
}
