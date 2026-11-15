import { cacheLife, cacheTag } from "next/cache";

import { fetchBook, fetchBooks } from "@/lib/catalog/operations/server";
import type { BookQuery } from "@/lib/catalog/types";
import type { ProductCard, ProductDetails, ProductPage } from "@/lib/product/types";

// Only valid inside a cache directive scope.
export function tagProducts(products: Array<{ handle: string }>): void {
  for (const product of products) cacheTag(`product-${product.handle}`);
}

export async function getProduct(params: { handle: string }): Promise<ProductDetails | undefined> {
  "use cache";
  cacheLife("hours");
  cacheTag("products", `product-${params.handle}`);

  return fetchBook(params.handle);
}

// Fixed grids only; browse results are cached by (collection, sort) in lib/collections/server.ts.
export async function getSearchIndexProducts(
  params: Pick<BookQuery, "limit">,
): Promise<ProductPage> {
  "use cache";
  cacheLife("hours");
  cacheTag("products", "products-index");

  const result = await fetchBooks({ limit: params.limit });
  tagProducts(result.products);
  return result;
}

// Other published books, newest first, with the current one left out.
export async function getProductRecommendations(params: {
  handle: string;
}): Promise<ProductCard[]> {
  "use cache";
  cacheLife("hours");
  cacheTag("products", `recommendations-${params.handle}`);

  const { products } = await fetchBooks({ limit: 8 });
  return products.filter((product) => product.handle !== params.handle);
}
