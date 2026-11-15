import { createCatalogClient } from "@/lib/catalog/supabase/server";
import {
  isNewBook,
  NEW_WINDOW_DAYS,
  toProductCard,
  toProductDetails,
} from "@/lib/catalog/transforms";
import { BOOK_COLUMNS, type BookQuery, type BookRow } from "@/lib/catalog/types";
import type { CollectionDefinition } from "@/lib/collections/definitions";
import type { Image } from "@/lib/media/types";
import type { ProductDetails, ProductPage } from "@/lib/product/types";

type CollectionHandle = CollectionDefinition["handle"];

export interface CollectionCount {
  count: number;
  thumbnail: Image | null;
  thumbnailProductId: string | null;
}

// Commas, parentheses and wildcards would change the meaning of a PostgREST filter.
function toSearchPattern(query: string): string {
  return `%${query.replace(/[\s,()%_*\\"']+/g, " ").trim()}%`;
}

function belongsTo(row: BookRow, handle: string, now: number): boolean {
  if (handle === "free") return row.access === "free";
  if (handle === "members") return row.access === "members" || (row.access === "paid" && row.member_included);
  if (handle === "paid") return row.access === "paid";
  if (handle === "new") return isNewBook(row, now);
  return handle === "all";
}

export async function fetchBooks(params: BookQuery = {}): Promise<ProductPage> {
  const now = Date.now();
  let request = createCatalogClient().from("books").select(BOOK_COLUMNS).eq("published", true);

  const { collection, query, sort } = params;
  if (collection === "free") request = request.eq("access", "free");
  if (collection === "paid") request = request.eq("access", "paid");
  if (collection === "members") {
    request = request.or("access.eq.members,and(access.eq.paid,member_included.eq.true)");
  }
  if (collection === "new") {
    request = request.gte("published_at", new Date(now - NEW_WINDOW_DAYS * 86_400_000).toISOString());
  }
  if (query?.trim()) {
    const pattern = toSearchPattern(query);
    request = request.or(`title.ilike.${pattern},subtitle.ilike.${pattern},blurb.ilike.${pattern}`);
  }

  if (sort === "title-ascending") request = request.order("title", { ascending: true });
  else if (sort === "title-descending") request = request.order("title", { ascending: false });
  else if (sort === "price-ascending") {
    request = request.order("price_cents", { ascending: true, nullsFirst: true });
  } else if (sort === "price-descending") {
    request = request.order("price_cents", { ascending: false, nullsFirst: false });
  } else request = request.order("published_at", { ascending: false, nullsFirst: false });

  if (params.limit) request = request.limit(params.limit);
  const { data, error } = await request;
  if (error) throw new Error(`Catalog query failed: ${error.message}`);
  return { products: ((data ?? []) as BookRow[]).map((row) => toProductCard(row, now)) };
}

export async function fetchBook(handle: string): Promise<ProductDetails | undefined> {
  const { data, error } = await createCatalogClient()
    .from("books")
    .select(BOOK_COLUMNS)
    .eq("slug", handle)
    .eq("published", true)
    .maybeSingle();
  if (error) throw new Error(`Catalog query failed: ${error.message}`);
  return data ? toProductDetails(data as BookRow, Date.now()) : undefined;
}

export async function fetchCollectionCounts(): Promise<Record<CollectionHandle, CollectionCount>> {
  const now = Date.now();
  const { data, error } = await createCatalogClient()
    .from("books")
    .select(BOOK_COLUMNS)
    .eq("published", true)
    .order("published_at", { ascending: false, nullsFirst: false });
  if (error) throw new Error(`Catalog query failed: ${error.message}`);
  const rows = (data ?? []) as BookRow[];
  const counts = {} as Record<CollectionHandle, CollectionCount>;
  for (const handle of ["new", "free", "members", "paid"] as const) {
    const matching = rows.filter((row) => belongsTo(row, handle, now));
    const first = matching[0];
    counts[handle] = {
      count: matching.length,
      thumbnail: first ? (toProductCard(first, now).featuredImage ?? null) : null,
      thumbnailProductId: first?.id ?? null,
    };
  }
  return counts;
}

export async function fetchSitemapBooks(): Promise<Array<{ handle: string; updatedAt: string }>> {
  const { data, error } = await createCatalogClient()
    .from("books")
    .select("slug, published_at")
    .eq("published", true)
    .order("published_at", { ascending: false, nullsFirst: false });
  if (error) throw new Error(`Catalog query failed: ${error.message}`);
  const fallback = new Date(0).toISOString();
  return (data ?? []).map((row) => ({
    handle: row.slug as string,
    updatedAt: (row.published_at as string | null) ?? fallback,
  }));
}
