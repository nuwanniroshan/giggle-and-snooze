import type { Image, Video } from "@/lib/media/types";
import type { Money } from "@/lib/money/types";
import type { SEO } from "@/lib/seo/types";

export type BookAccess = "free" | "members" | "paid";

export interface ProductCard {
  access: BookAccess;
  badge: string | null;
  featuredImage: Image | null;
  handle: string;
  id: string;
  price: Money;
  priceLabel: string;
  subtitle?: string;
  title: string;
  vendor?: string;
}

export interface RecentlyViewedProduct {
  featuredImage: Image | null;
  handle: string;
  id: string;
  price: Money;
  priceLabel: string;
  title: string;
}

export interface ProductPage {
  products: ProductCard[];
}

export interface ProductDetails extends ProductCard {
  currencyCode: string;
  description: string;
  hasSample: boolean;
  images: Image[];
  memberIncluded: boolean;
  pageCount?: number;
  seo: SEO;
  updatedAt: string;
  videos: Video[];
}
