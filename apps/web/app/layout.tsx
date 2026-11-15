import type { Metadata, Viewport } from "next";
import { Fredoka, Geist_Mono, Nunito } from "next/font/google";

import "./globals.css";

import { Footer } from "@/components/footer";
import { Nav } from "@/components/nav";
import { SiteSchema } from "@/components/schema/site-schema";
import { shopConfig } from "@/lib/config";

export const ensureStatic = "prefetch";

export const viewport: Viewport = {
  themeColor: "#fff8ee",
};

const fredoka = Fredoka({
  variable: "--font-fredoka",
  subsets: ["latin"],
});

const nunito = Nunito({
  variable: "--font-nunito",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang={shopConfig.localization.locale}>
      <body
        className={`${fredoka.variable} ${nunito.variable} ${geistMono.variable} flex min-h-dvh flex-col font-sans antialiased`}
      >
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-100 focus:rounded-md focus:bg-background focus:px-5 focus:py-2 focus:text-sm focus:font-medium focus:shadow-lg focus:ring-2 focus:ring-foreground focus:outline-none"
        >
          Skip to content
        </a>
        <SiteSchema />

        <Nav />
        <main id="main-content" className="flex flex-1 flex-col min-w-0">
          {children}
        </main>
        <Footer />
      </body>
    </html>
  );
}

export const generateMetadata = async (): Promise<Metadata> => {
  const title = `${shopConfig.site.name}: ${shopConfig.site.tagline}`;
  return {
    description: shopConfig.site.description,
    generator: shopConfig.site.name,
    metadataBase: new URL(shopConfig.site.url),
    openGraph: {
      description: shopConfig.site.description,
      images: [{ url: "/og-default.png", width: 1200, height: 628 }],
      siteName: shopConfig.site.name,
      title,
      type: "website",
      url: shopConfig.site.url,
    },
    title: {
      default: title,
      template: `%s | ${shopConfig.site.name}`,
    },
    twitter: { card: "summary_large_image", description: shopConfig.site.description, title },
  };
};
