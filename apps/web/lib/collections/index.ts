import type { BrowseState, Collection, SortValue } from "@/lib/collections/types";
import { shopConfig } from "@/lib/config";
import { summarizeText } from "@/lib/content";

export const PRODUCTS_PER_PAGE = 40;

export function describeCollection(collection: Collection): string {
  return (
    summarizeText(collection.seo.description) ||
    summarizeText(collection.description) ||
    `Browse ${collection.title} at ${shopConfig.site.name}.`
  );
}

// "manual" is the default order: newest first.
export const SORT_VALUES = [
  "manual",
  "title-ascending",
  "title-descending",
  "price-ascending",
  "price-descending",
] as const;

export const SEARCH_SORT_EXCLUDE: SortValue[] = [];

function isSortValue(value: string | null | undefined): value is SortValue {
  return SORT_VALUES.includes(value as SortValue);
}

export function resolveBrowseParams(search: string | URLSearchParams): BrowseState {
  const params = typeof search === "string" ? new URLSearchParams(search) : search;
  const raw = params.get("sort");
  const sort = isSortValue(raw) && raw !== "manual" ? raw : undefined;
  return { dataSearch: sort ? `sort=${sort}` : "", sort };
}
