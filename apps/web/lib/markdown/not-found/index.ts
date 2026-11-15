import { shopConfig } from "@/lib/config";

export function notFoundMarkdown({ kind }: { kind: "Collection" | "Product" }): string {
  const { url } = shopConfig.site;

  return `# ${kind} Not Found

The requested ${kind.toLowerCase()} could not be found.

## Continue browsing

- [Browse all books](${url}/collections/all)
- [Storefront guide](${url}/llms.txt)
- [Sitemap](${url}/sitemap.xml)
`;
}
