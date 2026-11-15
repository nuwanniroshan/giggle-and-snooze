import type { Metadata } from "next";

import { ProductsGrid } from "@/components/product/products-grid";
import { Container } from "@/components/ui/container";
import { Link } from "@/components/ui/link";
import { Page } from "@/components/ui/page";
import { Sections } from "@/components/ui/sections";
import { shopConfig } from "@/lib/config";
import { buildAlternates, buildOpenGraph } from "@/lib/seo";

export async function generateMetadata(): Promise<Metadata> {
  const title = "Home";
  const description = shopConfig.site.description;
  return {
    title: `${title} | ${shopConfig.site.name}`,
    description,
    alternates: buildAlternates({ pathname: "/" }),
    openGraph: buildOpenGraph({
      title,
      description,
      url: "/",
      type: "website",
    }),
  };
}

export default function HomePage() {
  return (
    <Page className="pt-0">
      <Sections>
        <section className="relative overflow-hidden bg-night text-night-foreground bg-[radial-gradient(circle,rgba(255,248,238,0.35)_1.5px,transparent_2px)] [background-size:44px_44px]">
          <div className="relative flex flex-col items-center text-center gap-5 px-5 py-14 md:py-24 lg:px-10">
            <img
              src="/brand/giggle-and-snooze-logo.png"
              alt={shopConfig.site.name}
              width={1256}
              height={733}
              className="w-72 h-auto md:w-[26rem] drop-shadow-[0_8px_0_rgba(0,0,0,0.25)]"
            />
            <h1 className="text-4xl md:text-6xl max-w-3xl text-night-foreground">
              {shopConfig.site.tagline}
            </h1>
            <p className="text-base md:text-lg max-w-xl text-night-foreground/85">
              {shopConfig.site.description}
            </p>
            <Link
              href="/collections/all"
              className="mt-2 inline-flex items-center rounded-full bg-brand-yellow px-8 py-3 font-display text-lg text-night shadow-[0_6px_0_0_#c98a00] transition-transform hover:-translate-y-0.5 active:translate-y-1 active:shadow-none"
            >
              Start reading
            </Link>
          </div>
        </section>

        <Container>
          <ProductsGrid
            title="Latest books"
            eagerCount={4}
            limit={8}
            collectionUrl="/collections/all"
          />
        </Container>
      </Sections>
    </Page>
  );
}
