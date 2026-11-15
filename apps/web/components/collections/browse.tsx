import { type ReactNode, Suspense } from "react";

import { ProductCard } from "@/components/product-card/product-card";
import { ProductsGridSkeleton } from "@/components/product/products-grid";
import { PRODUCTS_PER_PAGE } from "@/lib/collections";
import type { BrowseResults, SortValue } from "@/lib/collections/types";

import { BrowseToolbar } from "./toolbar";

interface BrowseProps {
  path: string;
  resultCount?: ReactNode;
  results: Promise<BrowseResults>;
  sortExclude?: SortValue[];
}

interface CollectionResultsProps {
  results: BrowseResults | Promise<BrowseResults>;
}

export function Browse({ path, resultCount, results, sortExclude }: BrowseProps) {
  return (
    <>
      <BrowseToolbar path={path} resultCount={resultCount} sortExclude={sortExclude} />
      <Suspense fallback={<BrowseResultsFallback />}>
        <CollectionResults results={results} />
      </Suspense>
    </>
  );
}

export function BrowseResultsFallback() {
  return (
    <ProductsGridSkeleton
      count={PRODUCTS_PER_PAGE}
      className="sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5"
    />
  );
}

export async function CollectionResults({ results }: CollectionResultsProps) {
  const { products, source } = await results;
  if (products.length === 0) {
    const query = source.type === "search" ? source.query : undefined;
    return (
      <div className="py-10 text-center">
        <h2 className="mb-2 text-2xl">No books found</h2>
        <p className="text-muted-foreground">
          {query ? `We couldn't find any books matching "${query}"` : "No books available yet"}
        </p>
      </div>
    );
  }
  return (
    <div className="grid grid-cols-2 gap-5 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
      {products.map((product, index) => (
        <ProductCard key={product.id} loading={index < 5 ? "eager" : "lazy"} product={product} />
      ))}
    </div>
  );
}
