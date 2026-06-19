import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "Secure Contract Intelligence & SLA Auditor",
  description: "Tenant-scoped contract auditing and cross-document QA.",
};

/** Root layout with the primary navigation shell. */
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <nav className="nav">
          <span className="brand">⚖️ Contract Intelligence</span>
          <Link href="/upload">Upload</Link>
          <Link href="/standards">Standards</Link>
          <Link href="/qa">Ask</Link>
        </nav>
        <main className="container">{children}</main>
      </body>
    </html>
  );
}
