import type { Metadata } from "next";
import Link from "next/link";

import "./globals.css";
import { site } from "@/lib/site";

const description = "Illustrated bedtime stories to read, giggle at and fall asleep to.";

export const metadata: Metadata = {
  metadataBase: new URL(site.url),
  title: { default: `${site.name}: ${site.tagline}`, template: `%s | ${site.name}` },
  description,
  openGraph: {
    type: "website",
    siteName: site.name,
    title: `${site.name}: ${site.tagline}`,
    description,
    url: site.url,
  },
  twitter: { card: "summary_large_image", title: site.name, description },
};

const footerLinks = [
  { href: "/books", label: "Books" },
  { href: "/membership", label: "Membership" },
  { href: "/terms", label: "Terms" },
  { href: "/privacy", label: "Privacy" },
  { href: "/refunds", label: "Refunds" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="flex min-h-dvh flex-col font-sans antialiased">
        <header className="border-b bg-secondary/40">
          <nav className="mx-auto flex w-full max-w-6xl items-center justify-between px-4 py-4">
            <Link href="/" className="text-lg font-semibold tracking-tight">
              <span aria-hidden="true">🌙 </span>
              {site.name}
            </Link>
            <div className="flex gap-6 text-sm">
              <Link href="/books" className="hover:underline">Books</Link>
              <Link href="/membership" className="hover:underline">Membership</Link>
              <Link href="/account" className="hover:underline">Account</Link>
            </div>
          </nav>
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-10">{children}</main>
        <footer className="border-t bg-secondary/40 py-8 text-sm text-muted-foreground">
          <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 sm:flex-row sm:items-center sm:justify-between">
            <p>
              © {new Date().getFullYear()} {site.name}. {site.tagline}
            </p>
            <ul className="flex flex-wrap gap-4">
              {footerLinks.map((l) => (
                <li key={l.href}>
                  <Link href={l.href} className="hover:underline">{l.label}</Link>
                </li>
              ))}
            </ul>
          </div>
        </footer>
      </body>
    </html>
  );
}
