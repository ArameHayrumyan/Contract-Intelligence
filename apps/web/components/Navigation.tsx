"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { UploadModal } from "@/components/UploadModal";
import { getRenewals } from "@/lib/api-client";

const LINKS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/standards", label: "Standards" },
  { href: "/qa", label: "Ask" },
  { href: "/monitoring", label: "Monitoring", monitor: true },
  { href: "/activity", label: "Activity" },
];

/** Top navigation: brand, active-aware links, monitoring alert dot, mobile menu. */
export function Navigation() {
  const pathname = usePathname();
  const [atRisk, setAtRisk] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);

  useEffect(() => {
    getRenewals([30, 60, 90], false)
      .then((r) => setAtRisk(r.total_at_risk))
      .catch(() => setAtRisk(0));
  }, []);

  // Close the mobile menu on navigation.
  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  function isActive(href: string) {
    return pathname === href || pathname.startsWith(`${href}/`);
  }

  // No chrome on the access gate or the root redirect.
  if (pathname === "/access" || pathname === "/") return null;

  return (
    <nav className="nav">
      <Link href="/dashboard" className="nav-brand">
        <ShieldIcon />
        Contract Intelligence
      </Link>

      <button
        className="nav-hamburger"
        onClick={() => setMenuOpen((v) => !v)}
        aria-label="Toggle menu"
      >
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
          <path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" strokeWidth="2" />
        </svg>
      </button>

      <div className={`nav-links${menuOpen ? " is-open" : ""}`}>
        <Link
          href="/dashboard"
          className={`nav-link${isActive("/dashboard") ? " is-active" : ""}`}
        >
          Dashboard
        </Link>
        <button
          className="nav-link"
          onClick={() => {
            setUploadOpen(true);
            setMenuOpen(false);
          }}
        >
          Upload
        </button>
        {LINKS.filter((l) => l.href !== "/dashboard").map((link) => (
          <Link
            key={link.href}
            href={link.href}
            className={`nav-link${isActive(link.href) ? " is-active" : ""}`}
          >
            {link.label}
            {link.monitor && atRisk > 0 ? (
              <span className="nav-dot" title={`${atRisk} at risk`} />
            ) : null}
          </Link>
        ))}
      </div>

      <UploadModal isOpen={uploadOpen} onClose={() => setUploadOpen(false)} />
    </nav>
  );
}

function ShieldIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M12 2l8 3v6c0 5-3.5 8.5-8 11-4.5-2.5-8-6-8-11V5l8-3z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  );
}
