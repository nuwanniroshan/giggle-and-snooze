import type { Metadata, Viewport } from "next";
import { Geist_Mono, Nunito, Schoolbell } from "next/font/google";

import "./globals.css";

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000";

export const viewport: Viewport = {
  themeColor: "#fffdf8",
};

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: "Giggle & Snooze",
    template: "%s | Giggle & Snooze",
  },
  description:
    "A playful children's bookstore where kids can discover, preview, and read storybooks in PDF or ePUB, with free and paid membership options and linked story videos.",
  icons: {
    icon: "/favicon.svg",
  },
  openGraph: {
    title: "Giggle & Snooze",
    description:
      "Joyful digital storybooks for curious kids and cozy bedtimes.",
    images: [{ url: "/og-default.png", width: 1200, height: 628 }],
    siteName: "Giggle & Snooze",
    type: "website",
    url: siteUrl,
  },
};

const nunito = Nunito({
  subsets: ["latin"],
  variable: "--font-nunito",
});

const schoolbell = Schoolbell({
  subsets: ["latin"],
  weight: "400",
  variable: "--font-schoolbell",
});

const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
});

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${nunito.variable} ${schoolbell.variable} ${geistMono.variable}`}
    >
      <body className="min-h-screen bg-[#fffdf8] font-sans text-[#173f47] antialiased">
        <div id="root" tabIndex={-1}>
          {children}
        </div>
      </body>
    </html>
  );
}
