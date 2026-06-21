import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import type { ReactNode } from "react";

import { Navigation } from "@/components/Navigation";
import { PageFade } from "@/components/PageFade";
import { RouteProgress } from "@/components/RouteProgress";
import { ToastProvider } from "@/components/ui/Toast";

import "../styles/design-tokens.css";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});
const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Secure Contract Intelligence & SLA Auditor",
  description: "Tenant-scoped contract auditing and cross-document QA.",
};

/** Root layout: fonts, design tokens, toast provider, nav, and page chrome. */
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrains.variable}`}>
      <body>
        <ToastProvider>
          <RouteProgress />
          <Navigation />
          <main>
            <PageFade>{children}</PageFade>
          </main>
        </ToastProvider>
      </body>
    </html>
  );
}
