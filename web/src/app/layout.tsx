import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { SiteNav } from "@/components/SiteNav";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    template: "%s",
    default: "CCPO -- Credit Card Portfolio Optimiser",
  },
  description: "Compare credit cards and optimise your card portfolio against real reward rules.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} antialiased`}
    >
      {/* No `h-full` on <html>: that forces height:100% (a HARD cap at
          the initial viewport height), which turns <html> into its OWN
          inner scroll container the instant page content (e.g. a card's
          full rule breakdown) exceeds one screen -- the page then has
          two nested scroll positions instead of one, and CDP-driven
          screenshots/automation can capture a stale one. `min-h-full` on
          body is enough to keep short pages full-height without creating
          that trap. Found by comparing a screenshot against `getBoundingClientRect()`
          on this page's own detail view -- they visually disagreed until
          this was removed. */}
      <body className="min-h-full flex flex-col">
        <SiteNav />
        {children}
      </body>
    </html>
  );
}
