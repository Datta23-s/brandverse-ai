import type { Metadata } from "next";
import { Inter, Instrument_Serif, JetBrains_Mono } from "next/font/google";
import Link from "next/link";
import type { ReactNode } from "react";

import { AccountMenu } from "@/components/auth/AccountMenu";

import { Providers } from "./providers";
import "./globals.css";

// Design.md §4: Instrument Serif for the argument, Inter for the interface, JetBrains Mono for data.
const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
const instrumentSerif = Instrument_Serif({
  subsets: ["latin"],
  weight: "400",
  variable: "--font-instrument-serif",
  display: "swap",
});
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "BrandverseAI",
  description: "Campaign intelligence that reuses what it already knows.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${instrumentSerif.variable} ${jetbrainsMono.variable}`}
    >
      <body className="min-h-dvh antialiased">
        <Providers>
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-bv-sm focus:bg-bv-surface focus:px-4 focus:py-2"
        >
          Skip to main content
        </a>
        <header className="border-b border-bv-border">
          <nav className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4 sm:px-6">
            <Link href="/" className="font-display text-2xl tracking-tight text-bv-text">
              BrandverseAI
            </Link>
            <AccountMenu />
          </nav>
        </header>
          <main id="main" className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-12">
            {children}
          </main>
        </Providers>
      </body>
    </html>
  );
}
