import { shopConfig } from "@/lib/config";
import { escapeMarkdown, markdownFrontmatter } from "@/lib/markdown";
import type { ProductDetails } from "@/lib/product/types";

const ACCESS_LINE = {
  free: "Free to read after signing in.",
  members: "Included with a membership.",
  paid: "One-time purchase; members also read it when it is member-included.",
} as const;

export function productToMarkdown(product: ProductDetails): string {
  const siteUrl = shopConfig.site.url;
  const url = new URL(`/products/${product.handle}`, siteUrl);
  const sections: string[] = [
    markdownFrontmatter({
      canonicalUrl: url.toString(),
      description: product.seo.description,
      lastUpdated: product.updatedAt,
      title: product.title,
    }),
    "",
    `# ${escapeMarkdown(product.title)}`,
    "",
  ];

  if (product.subtitle) sections.push(`*${escapeMarkdown(product.subtitle)}*`, "");
  if (product.vendor) sections.push(escapeMarkdown(product.vendor), "");
  if (product.seo.description) sections.push(`> ${escapeMarkdown(product.seo.description)}`, "");

  const facts = [`**${escapeMarkdown(product.priceLabel)}**`, ACCESS_LINE[product.access]];
  if (product.pageCount) facts.push(`${product.pageCount} pages`);
  sections.push(facts.join(" · "), "");

  if (product.description) sections.push("## About", "", escapeMarkdown(product.description), "");

  if (product.images.length > 0) {
    sections.push("## Images", "");
    for (const image of product.images) {
      sections.push(`- ![${escapeMarkdown(image.altText)}](${image.url})`);
    }
    sections.push("");
  }

  sections.push(
    "## Read",
    "",
    `- Book page: ${url}`,
    "",
    "---",
    "",
    `*Last updated: ${product.updatedAt.slice(0, 10)}*`,
  );

  return sections.join("\n");
}
