import type { MetadataRoute } from "next";

import { listBooks } from "@/lib/books";
import { site } from "@/lib/site";

export const dynamic = "force-dynamic";

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const pages = ["", "/books", "/membership", "/terms", "/privacy", "/refunds"].map((p) => ({ url: `${site.url}${p}` }));
  let books: MetadataRoute.Sitemap = [];
  try {
    books = (await listBooks()).map((b) => ({ url: `${site.url}/books/${b.slug}` }));
  } catch {
    // Catalog unavailable: list static pages only.
  }
  return [...pages, ...books];
}
