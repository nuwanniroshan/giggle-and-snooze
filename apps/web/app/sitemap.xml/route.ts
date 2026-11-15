import { fetchSitemapBooks } from "@/lib/catalog/operations/server";
import { COLLECTION_DEFINITIONS } from "@/lib/collections/definitions";
import { shopConfig } from "@/lib/config";

function escapeXml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export async function GET(): Promise<Response> {
  const books = await fetchSitemapBooks().catch(() => []);
  const paths: Array<{ lastmod?: string; path: string }> = [
    { path: "/" },
    { path: "/collections" },
    { path: "/collections/all" },
    ...COLLECTION_DEFINITIONS.map(({ handle }) => ({ path: `/collections/${handle}` })),
    { path: "/membership" },
    ...books.map(({ handle, updatedAt }) => ({ lastmod: updatedAt, path: `/products/${handle}` })),
  ];
  const entries = paths
    .map(({ lastmod, path }) => {
      const loc = escapeXml(`${shopConfig.site.url}${path}`);
      return lastmod
        ? `  <url>\n    <loc>${loc}</loc>\n    <lastmod>${escapeXml(lastmod)}</lastmod>\n  </url>`
        : `  <url><loc>${loc}</loc></url>`;
    })
    .join("\n");

  return new Response(
    `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${entries}
</urlset>
`,
    { headers: { "Content-Type": "application/xml; charset=utf-8" } },
  );
}
