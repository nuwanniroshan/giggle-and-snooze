import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import {
  ProductDetailSection,
  ProductDetailSkeleton,
} from "@/components/product-detail/product-detail-section";
import { RecentlyViewedRecorder } from "@/components/product-detail/recently-viewed";
import { RelatedProductsSection } from "@/components/product/related-products-section";
import { Container } from "@/components/ui/container";
import { Page } from "@/components/ui/page";
import { Sections } from "@/components/ui/sections";
import { getProduct, getSearchIndexProducts } from "@/lib/product/server";
import { buildAlternates, buildOpenGraph } from "@/lib/seo";
import { PLACEHOLDER_HANDLE } from "@/lib/static-params";

async function buildProductMetadata(handle: string, canonicalPath: string): Promise<Metadata> {
  const product = await getProduct({ handle });
  if (!product) notFound();
  const images = product.featuredImage
    ? [
        {
          alt: product.featuredImage.altText,
          height: product.featuredImage.height,
          url: product.featuredImage.url,
          width: product.featuredImage.width,
        },
      ]
    : ["/og-default.png"];
  return {
    alternates: buildAlternates({ pathname: canonicalPath }),
    description: product.seo.description,
    openGraph: buildOpenGraph({
      description: product.seo.description,
      images,
      title: product.seo.title,
      url: canonicalPath,
    }),
    title: product.seo.title,
    twitter: {
      card: "summary_large_image",
      description: product.seo.description,
      images,
      title: product.seo.title,
    },
  };
}

export async function generateStaticParams() {
  try {
    const { products } = await getSearchIndexProducts({ limit: 1 });
    const first = products[0];
    return [{ handle: first ? first.handle : PLACEHOLDER_HANDLE }];
  } catch {
    return [{ handle: PLACEHOLDER_HANDLE }];
  }
}

export async function generateMetadata({
  params,
}: PageProps<"/products/[handle]">): Promise<Metadata> {
  const { handle } = await params;
  if (handle === PLACEHOLDER_HANDLE) return {};
  return buildProductMetadata(handle, `/products/${handle}`);
}

export default function ProductPage({ params }: PageProps<"/products/[handle]">) {
  return (
    <Page className="pt-0">
      <Container className="bg-background">
        <Sections>
          <Suspense fallback={<ProductDetailSkeleton />}>
            <ProductPageContent params={params} />
          </Suspense>
        </Sections>
      </Container>
    </Page>
  );
}

async function ProductPageContent({ params }: Pick<PageProps<"/products/[handle]">, "params">) {
  const { handle } = await params;
  if (handle === PLACEHOLDER_HANDLE) notFound();
  const product = await getProduct({ handle });
  if (!product) notFound();

  return (
    <>
      <RecentlyViewedRecorder
        product={{
          featuredImage: product.featuredImage,
          handle: product.handle,
          id: product.id,
          price: product.price,
          priceLabel: product.priceLabel,
          title: product.title,
        }}
      />
      <ProductDetailSection product={product} />
      <RelatedProductsSection handle={handle} limit={4} />
    </>
  );
}
