import { NextResponse } from "next/server";

import { fetchBooks } from "@/lib/catalog/operations/server";
import { SEARCH_TERM_MAX_LENGTH } from "@/lib/search";
import type { PredictiveSearchResult } from "@/lib/search/types";

export async function GET(request: Request) {
  const query = (new URL(request.url).searchParams.get("q") ?? "")
    .trim()
    .slice(0, SEARCH_TERM_MAX_LENGTH);
  const empty: PredictiveSearchResult = { products: [], queries: [] };
  if (!query) return NextResponse.json(empty);
  try {
    const { products } = await fetchBooks({ limit: 4, query });
    const result: PredictiveSearchResult = {
      products: products.map(({ featuredImage, handle, id, price, priceLabel, title }) => ({
        featuredImage,
        handle,
        id,
        price,
        priceLabel,
        title,
      })),
      queries: [],
    };
    return NextResponse.json(result);
  } catch {
    return NextResponse.json(empty, { status: 502 });
  }
}
