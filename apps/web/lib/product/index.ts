import type { ProductDetails } from "@/lib/product/types";

export function buildProductUrl(handle: string): string {
  return `/products/${handle}`;
}

export function getBookFacts(product: Pick<ProductDetails, "pageCount" | "priceLabel">): string {
  return [product.priceLabel, product.pageCount ? `${product.pageCount} pages` : null]
    .filter(Boolean)
    .join(" · ");
}
