import type { SORT_VALUES } from "@/lib/collections";
import type { Image } from "@/lib/media/types";
import type { ProductPage } from "@/lib/product/types";
import type { SEO } from "@/lib/seo/types";

export interface BrowseState {
  dataSearch: string;
  sort?: SortValue;
}

export type BrowseSource =
  | { collection: string; type: "collection" }
  | { query?: string; type: "search" };

export interface BrowseResults extends ProductPage {
  dataSearch: string;
  source: BrowseSource;
  total?: number;
}

export interface Collection {
  description: string;
  handle: string;
  id?: string;
  image?: Image | null;
  path: string;
  seo: SEO;
  title: string;
  updatedAt?: string;
}

export interface CollectionWithThumbnail extends Collection {
  thumbnail: Image | null;
  thumbnailProductId: string | null;
}

export type SortValue = (typeof SORT_VALUES)[number];
