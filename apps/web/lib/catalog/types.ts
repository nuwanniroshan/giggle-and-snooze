import type { BookAccess } from "@/lib/product/types";

export interface BookRow {
  access: BookAccess;
  blurb: string | null;
  cover_path: string | null;
  currency: string;
  id: string;
  member_included: boolean;
  page_count: number | null;
  price_cents: number | null;
  published_at: string | null;
  sample_epub_path: string | null;
  slug: string;
  subtitle: string | null;
  title: string;
}

export const BOOK_COLUMNS =
  "id, slug, title, subtitle, blurb, access, member_included, price_cents, currency, page_count, cover_path, sample_epub_path, published_at";

export interface BookQuery {
  collection?: string;
  limit?: number;
  query?: string;
  sort?: string;
}
