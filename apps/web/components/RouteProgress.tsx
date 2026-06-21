"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

/** Thin top progress bar that animates on each route change (no library). */
export function RouteProgress() {
  const pathname = usePathname();
  const [width, setWidth] = useState(0);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    setVisible(true);
    setWidth(30);
    const t1 = setTimeout(() => setWidth(70), 100);
    const t2 = setTimeout(() => setWidth(100), 300);
    const t3 = setTimeout(() => {
      setVisible(false);
      setWidth(0);
    }, 600);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
      clearTimeout(t3);
    };
  }, [pathname]);

  return (
    <div
      className="route-progress"
      style={{ width: `${width}%`, opacity: visible ? 1 : 0 }}
    />
  );
}
