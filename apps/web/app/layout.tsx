import type { Metadata } from "next";
import Link from "next/link";

import "./globals.css";
import { site } from "@/lib/site";

export const metadata: Metadata = {
  metadataBase: new URL(site.url),
  title: { default: `${site.name}: ${site.tagline}`, template: `%s | ${site.name}` },
  description: "Illustrated bedtime stories to read, giggle at and fall asleep to.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="flex min-h-dvh flex-col font-sans antialiased">
        <header className="border-b">
          <nav className="mx-auto flex w-full max-w-6xl items-center justify-between px-4 py-4">
            <Link href="/" className="text-lg font-semibold">{site.name}</Link>
            <div className="flex gap-6 text-sm">
              <Link href="/books">Books</Link>
              <Link href="/membership">Membership</Link>
              <Link href="/account">Account</Link>
            </div>
          </nav>
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-10">{children}</main>
        <footer className="border-t py-6 text-center text-xs text-muted-foreground">
          © {new Date().getFullYear()} {site.name}
        </footer>
      </body>
    </html>
  );
}
