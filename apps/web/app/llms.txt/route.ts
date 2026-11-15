import { getCollections } from "@/lib/collections/server";
import { shopConfig } from "@/lib/config";
import { summarizeText } from "@/lib/content";
import { escapeMarkdown } from "@/lib/markdown";

export async function GET(): Promise<Response> {
  const { description, name, url } = shopConfig.site;
  const collections = await getCollections({ limit: 50 }).catch(() => []);
  const collectionLinks = collections.map((collection) => {
    const link = `[${escapeMarkdown(collection.title)}](${url}${collection.path})`;
    const summary = summarizeText(collection.description);
    return summary ? `- ${link}: ${escapeMarkdown(summary)}` : `- ${link}`;
  });

  return new Response(
    `# ${escapeMarkdown(name)}

> ${escapeMarkdown(summarizeText(description))}

Home, book, collection, and search pages return Markdown with an \`Accept: text/markdown\` header or at the page URL with \`.md\` appended (\`/index.md\` for the home page). Books are read online after signing in; some are free, some need a membership or a one-time purchase.

## Browse

- [All books](${url}/collections/all): The full library.
- [Membership](${url}/membership): Read every member-included book.
${collectionLinks.length > 0 ? `\n## Collections\n\n${collectionLinks.join("\n")}\n` : ""}
## Discovery

- [Sitemap](${url}/sitemap.xml): Complete index of book and collection URLs.
- [Robots](${url}/robots.txt): Crawl policy.
`,
    {
      headers: {
        "Cache-Control": "public, max-age=86400, stale-while-revalidate=604800",
        "Content-Type": "text/plain; charset=utf-8",
      },
    },
  );
}
