"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

/** Fades page content in on mount and on every route change. */
export function PageFade({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(false);
    const id = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(id);
  }, [pathname]);

  return <div className={`fade-in${mounted ? " is-mounted" : ""}`}>{children}</div>;
}
