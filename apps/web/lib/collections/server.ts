import type { Metadata } from "next";
import { cacheLife, cacheTag } from "next/cache";

import {
  fetchBooks,
  fetchCollectionCounts,
} from "@/lib/catalog/operations/server";
import { describeCollection, PRODUCTS_PER_PAGE, resolveBrowseParams } from "@/lib/collections";
import { COLLECTION_DEFINITIONS } from "@/lib/collections/definitions";
import type {
  BrowseResults,
  BrowseState,
  Collection,
  CollectionWithThumbnail,
} from "@/lib/collections/types";
import { shopConfig } from "@/lib/config";
import { buildAlternates, buildOpenGraph } from "@/lib/seo";

// /collections/all is a virtual collection of every published book.
export const ALL_PRODUCTS_HANDLE = "all";

function toCollection(definition: (typeof COLLECTION_DEFINITIONS)[number]): Collection {
  return {
    description: definition.description,
    handle: definition.handle,
    path: `/collections/${definition.handle}`,
    seo: { description: definition.description, title: definition.title },
    title: definition.title,
  };
}

export async function getCollections(params: { limit?: number } = {}): Promise<Collection[]> {
  "use cache";
  cacheLife("hours");
  cacheTag("collections", "collections-index");

  const counts = await fetchCollectionCounts();
  return COLLECTION_DEFINITIONS.filter((definition) => counts[definition.handle].count > 0)
    .slice(0, params.limit)
    .map(toCollection);
}

export async function getCollection(params: { handle: string }): Promise<Collection | undefined> {
  "use cache";
  cacheLife("hours");
  cacheTag("collections", `collection-${params.handle}`);

  const definition = COLLECTION_DEFINITIONS.find(({ handle }) => handle === params.handle);
  return definition ? toCollection(definition) : undefined;
}

export async function getCollectionsListing(
  params: { limit?: number } = {},
): Promise<CollectionWithThumbnail[]> {
  "use cache";
  cacheLife("hours");
  cacheTag("collections", "collections-index", "products-index");

  const counts = await fetchCollectionCounts();
  return COLLECTION_DEFINITIONS.filter((definition) => counts[definition.handle].count > 0)
    .slice(0, params.limit)
    .map((definition) => ({
      ...toCollection(definition),
      thumbnail: counts[definition.handle].thumbnail,
      thumbnailProductId: counts[definition.handle].thumbnailProductId,
    }));
}

export async function readBrowseState(
  searchParamsPromise: Promise<Record<string, string | string[] | undefined>>,
): Promise<BrowseState> {
  const record = await searchParamsPromise;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(record)) {
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, item);
    } else if (value !== undefined) {
      params.set(key, value);
    }
  }
  return resolveBrowseParams(params);
}

// Sort is the only browse state, so each (collection, sort) pair is a small cache entry.
export async function getCollectionResults(params: {
  handle: string;
  sort?: string;
}): Promise<BrowseResults> {
  "use cache";
  cacheLife("hours");
  cacheTag("collections", `collection-${params.handle}`, "products-index");

  const { products } = await fetchBooks({
    collection: params.handle,
    limit: PRODUCTS_PER_PAGE,
    sort: params.sort,
  });
  return {
    dataSearch: params.sort ? `sort=${params.sort}` : "",
    products,
    source: { collection: params.handle, type: "collection" },
    total: products.length,
  };
}

export async function fetchCollectionResults({
  handle,
  statePromise,
}: {
  handle: string;
  statePromise: Promise<BrowseState>;
}): Promise<BrowseResults> {
  const { sort } = await statePromise;
  return getCollectionResults({ handle, sort });
}

export async function getAllProductsCollection(): Promise<Collection> {
  const title = "All books";
  const description = "Every story on the shelf.";
  return {
    description,
    handle: ALL_PRODUCTS_HANDLE,
    image: null,
    path: `/collections/${ALL_PRODUCTS_HANDLE}`,
    seo: {
      description: `Browse every illustrated bedtime story at ${shopConfig.site.name}.`,
      title,
    },
    title,
  };
}

function browseMetadata(handle: string, title: string, description: string): Metadata {
  const url = `/collections/${handle}`;
  return {
    alternates: buildAlternates({ pathname: url }),
    description,
    openGraph: buildOpenGraph({ description, title, type: "website", url }),
    title,
    twitter: {
      card: "summary_large_image",
      description,
      images: ["/og-default.png"],
      title,
    },
  };
}

export async function getCollectionMetadata(handle: string): Promise<Metadata> {
  const collection = await getCollection({ handle });
  if (!collection) return browseMetadata(handle, "Collection", "Browse a collection of books.");
  return browseMetadata(collection.handle, collection.seo.title, describeCollection(collection));
}

export async function getAllProductsMetadata(): Promise<Metadata> {
  return browseMetadata(
    ALL_PRODUCTS_HANDLE,
    "All books",
    describeCollection(await getAllProductsCollection()),
  );
}
