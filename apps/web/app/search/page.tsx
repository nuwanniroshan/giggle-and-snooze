import type { Metadata } from "next";
import { Suspense } from "react";

import { Browse } from "@/components/collections/browse";
import { BrowseFallback } from "@/components/collections/toolbar";
import { Container } from "@/components/ui/container";
import { Link } from "@/components/ui/link";
import { Page } from "@/components/ui/page";
import { Sections } from "@/components/ui/sections";
import { Skeleton } from "@/components/ui/skeleton";
import { SEARCH_SORT_EXCLUDE } from "@/lib/collections";
import { readBrowseState } from "@/lib/collections/server";
import type { BrowseResults } from "@/lib/collections/types";
import { formatCount } from "@/lib/content";
import { fetchSearchResults } from "@/lib/search/server";
import { buildAlternates, buildOpenGraph } from "@/lib/seo";

type SearchParams = Record<string, string | string[] | undefined>;

function getParam(searchParams: SearchParams, key: string): string | undefined {
  const value = searchParams[key];
  return Array.isArray(value) ? value[0] : value;
}

export async function generateMetadata({ searchParams }: PageProps<"/search">): Promise<Metadata> {
  const resolvedSearchParams = await searchParams;
  const query = getParam(resolvedSearchParams, "q") ?? "";
  const hasQuery = query.length > 0;
  const title = hasQuery ? `Search results for "${query}"` : "Search";
  const description = hasQuery
    ? `Find books matching "${query}"`
    : "Search the Giggle & Snooze library";
  return {
    title,
    description,
    alternates: buildAlternates({
      pathname: "/search",
      searchParams: resolvedSearchParams,
    }),
    openGraph: buildOpenGraph({
      title,
      description,
      url: "/search",
      type: "website",
    }),
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: ["/og-default.png"],
    },
    robots: {
      index: false,
      follow: false,
    },
  };
}

export default function SearchPage({ searchParams }: PageProps<"/search">) {
  // Don't await searchParams here — it would force the route fully dynamic.
  const statePromise = readBrowseState(searchParams);
  const resultsPromise = searchParams.then((resolved) =>
    fetchSearchResults({ query: getParam(resolved, "q"), statePromise }),
  );
  return (
    <Page className="pt-2.5 md:pt-10">
      <Container>
        <Sections className="gap-5">
          <div>
            <h1 className="text-3xl sm:text-4xl md:text-5xl">
              <Link href="/search">Search</Link>
              <Suspense fallback={null}>
                <SearchQueryLabel searchParamsPromise={searchParams} />
              </Suspense>
            </h1>
          </div>
          <Suspense fallback={<BrowseFallback resultCount={<Skeleton className="h-4 w-20" />} />}>
            <SearchBrowse resultsPromise={resultsPromise} />
          </Suspense>
        </Sections>
      </Container>
    </Page>
  );
}

function SearchBrowse({ resultsPromise }: { resultsPromise: Promise<BrowseResults> }) {
  return (
    <Browse
      path="/search"
      resultCount={
        <Suspense fallback={<Skeleton className="h-4 w-20" />}>
          <SearchResultCount resultsPromise={resultsPromise} />
        </Suspense>
      }
      results={resultsPromise}
      sortExclude={SEARCH_SORT_EXCLUDE}
    />
  );
}

async function SearchQueryLabel({
  searchParamsPromise,
}: {
  searchParamsPromise: PageProps<"/search">["searchParams"];
}) {
  const resolvedSearchParams = await searchParamsPromise;
  const query = getParam(resolvedSearchParams, "q");
  if (!query) return null;
  return ` for "${query}"`;
}

async function SearchResultCount({ resultsPromise }: { resultsPromise: Promise<BrowseResults> }) {
  const { total } = await resultsPromise;
  if (!total) return null;
  return formatCount(total, "book");
}
