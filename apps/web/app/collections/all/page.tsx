import type { Metadata } from "next";

import { CollectionResults } from "@/components/collections/browse";
import { CollectionBrowseBoundary } from "@/components/collections/collection-page";
import {
  ALL_PRODUCTS_HANDLE,
  fetchCollectionResults,
  getAllProductsMetadata,
  readBrowseState,
} from "@/lib/collections/server";

export async function generateMetadata(): Promise<Metadata> {
  return getAllProductsMetadata();
}

export default function AllProductsPage({ searchParams }: PageProps<"/collections/all">) {
  const statePromise = readBrowseState(searchParams);
  return (
    <CollectionBrowseBoundary>
      <CollectionResults
        results={fetchCollectionResults({ handle: ALL_PRODUCTS_HANDLE, statePromise })}
      />
    </CollectionBrowseBoundary>
  );
}
