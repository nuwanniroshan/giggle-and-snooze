import { Suspense } from "react";

import {
  BookActions,
  BookActionsSkeleton,
} from "@/components/product-detail/book-actions";
import { ProductOpenGraph } from "@/components/product-detail/open-graph";
import { ProductMedia } from "@/components/product-detail/product-media";
import { ProductPrice } from "@/components/product-detail/product-price";
import { ProductSchema } from "@/components/product-detail/schema";
import { BreadcrumbSchema } from "@/components/schema/breadcrumb-schema";
import { Link } from "@/components/ui/link";
import { Skeleton } from "@/components/ui/skeleton";
import { shopConfig } from "@/lib/config";
import { getBookFacts } from "@/lib/product";
import type { ProductDetails } from "@/lib/product/types";

export function ProductDetailSection({ product }: { product: ProductDetails }) {
  return (
    <>
      <ProductSchema
        product={{
          currencyCode: product.currencyCode,
          description: product.description,
          handle: product.handle,
          images: product.images,
          price: product.price.amount,
          title: product.title,
          vendor: product.vendor,
        }}
      />
      <ProductOpenGraph price={product.price} />
      <BreadcrumbSchema
        items={[
          { name: shopConfig.site.name, path: "/" },
          { name: product.title, path: `/products/${product.handle}` },
        ]}
      />
      <div className="grid gap-10 lg:grid-cols-10 lg:items-start lg:gap-5">
        <ProductMedia
          otherImages={product.images}
          videos={product.videos}
          title={product.title}
          className="lg:col-span-6"
        />
        <ProductInfoArea product={product} />
      </div>
    </>
  );
}

function ProductInfoArea({ product }: { product: ProductDetails }) {
  return (
    <div className="grid gap-10 lg:sticky lg:top-20 lg:col-span-4">
      <div className="grid gap-10">
        <div data-slot="product-info-header" className="grid gap-1">
          {product.badge ? (
            <span className="w-fit rounded-full bg-secondary px-2.5 py-1 text-xs font-medium text-secondary-foreground">
              {product.badge}
            </span>
          ) : null}
          <h1 className="text-foreground text-3xl">{product.title}</h1>
          {product.subtitle ? <p className="text-muted-foreground">{product.subtitle}</p> : null}
          <ProductPrice
            access={product.access}
            amount={product.price.amount}
            currencyCode={product.price.currencyCode}
            label={product.priceLabel}
          />
          {product.pageCount ? (
            <p className="text-sm text-muted-foreground">{getBookFacts(product)}</p>
          ) : null}
        </div>

        <Suspense fallback={<BookActionsSkeleton />}>
          <BookActions product={product} />
        </Suspense>

        {product.memberIncluded && product.access !== "free" ? (
          <p className="rounded-lg bg-secondary/60 px-3 py-2 text-sm">
            <Link href="/membership" className="underline underline-offset-4">
              Included with membership
            </Link>
          </p>
        ) : null}
      </div>

      {product.description ? (
        <div data-slot="product-info-description">
          <p className="whitespace-pre-line text-sm leading-6 text-foreground/80">
            {product.description}
          </p>
        </div>
      ) : null}
    </div>
  );
}

export function ProductDetailSkeleton() {
  return (
    <div aria-busy="true" className="grid gap-10 lg:grid-cols-10 lg:items-start lg:gap-5">
      <div className="lg:col-span-6">
        <div className="grid gap-5 lg:hidden">
          <div className="-mx-5 aspect-square w-[calc(100%+2.5rem)] bg-accent" />
          <div aria-hidden="true" className="h-1.5" />
        </div>
        <div className="hidden grid-cols-2 gap-2.5 lg:grid">
          <div className="aspect-square w-full bg-accent" />
        </div>
      </div>
      <div className="grid gap-10 lg:sticky lg:top-20 lg:col-span-4">
        <div className="grid gap-10">
          <div>
            <Skeleton className="h-9 w-3/4" />
            <Skeleton className="h-7 w-24" />
          </div>
          <Skeleton className="h-12 w-full" />
        </div>
        <div className="grid gap-4">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-5/6" />
        </div>
      </div>
    </div>
  );
}
