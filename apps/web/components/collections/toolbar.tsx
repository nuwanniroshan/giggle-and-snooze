import { ChevronDownIcon } from "lucide-react";
import { type ReactNode, Suspense } from "react";

import { ProductsGridSkeleton } from "@/components/product/products-grid";
import { PRODUCTS_PER_PAGE } from "@/lib/collections";
import type { SortValue } from "@/lib/collections/types";

import { CollectionsSortSelect } from "./sort-select";

interface BrowseToolbarProps {
  path: string;
  resultCount?: ReactNode;
  sortExclude?: SortValue[];
}

export function BrowseToolbar({ path, resultCount, sortExclude }: BrowseToolbarProps) {
  return (
    <ToolbarLayout
      resultCount={resultCount}
      sortSelect={
        <Suspense fallback={<SortFallback />}>
          <CollectionsSortSelect exclude={sortExclude} path={path} />
        </Suspense>
      }
    />
  );
}

interface BrowseFallbackProps {
  resultCount?: ReactNode;
}

export function BrowseFallback({ resultCount }: BrowseFallbackProps) {
  return (
    <>
      <ToolbarLayout
        resultCount={resultCount}
        sortSelect={<SortFallback />}
      />
      <ProductsGridSkeleton
        count={PRODUCTS_PER_PAGE}
        className="sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5"
      />
    </>
  );
}

function SortFallback() {
  return (
    <div className="flex h-9 w-fit items-center justify-between gap-2 rounded-md bg-transparent px-0 py-2 text-sm whitespace-nowrap">
      <span>Sort</span>
      <ChevronDownIcon className="size-4 text-muted-foreground opacity-50" />
    </div>
  );
}

interface ToolbarLayoutProps {
  resultCount?: ReactNode;
  sortSelect: ReactNode;
}

function ToolbarLayout({ resultCount, sortSelect }: ToolbarLayoutProps) {
  return (
    <div className="flex items-center gap-5">
      {resultCount !== undefined && (
        <div className="hidden items-center text-sm text-muted-foreground sm:flex">
          {resultCount}
        </div>
      )}
      <div className="ml-auto flex items-center gap-5">{sortSelect}</div>
    </div>
  );
}
