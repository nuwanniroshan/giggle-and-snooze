import { connection } from "next/server";

import { fetchBooks } from "@/lib/catalog/operations/server";
import { PRODUCTS_PER_PAGE } from "@/lib/collections";
import type { BrowseResults, BrowseState } from "@/lib/collections/types";

export async function fetchSearchResults({
  query,
  statePromise,
}: {
  query?: string;
  statePromise: Promise<BrowseState>;
}): Promise<BrowseResults> {
  // Badges depend on the current time, so results are computed at request time.
  await connection();
  const { dataSearch, sort } = await statePromise;
  const { products } = await fetchBooks({ limit: PRODUCTS_PER_PAGE, query, sort });
  return {
    dataSearch,
    products,
    source: { query, type: "search" },
    total: products.length,
  };
}
