import type { BookRow } from "@/lib/catalog/types";
import { summarizeText } from "@/lib/content";
import type { Image } from "@/lib/media/types";
import { fromMinorUnits } from "@/lib/money";
import type { ProductCard, ProductDetails } from "@/lib/product/types";

export const NEW_WINDOW_DAYS = 60;

export function toCoverUrl(path: string | null): string | null {
  const base = process.env.NEXT_PUBLIC_SUPABASE_URL;
  if (!path || !base) return null;
  return `${base}/storage/v1/object/public/covers/${path}`;
}

function toCover(row: BookRow): Image | null {
  const url = toCoverUrl(row.cover_path);
  return url ? { altText: `Cover of ${row.title}`, height: 1200, url, width: 1200 } : null;
}

export function isNewBook(row: Pick<BookRow, "published_at">, now: number): boolean {
  if (!row.published_at) return false;
  return now - new Date(row.published_at).getTime() < NEW_WINDOW_DAYS * 86_400_000;
}

export function getPriceLabel(row: Pick<BookRow, "access" | "currency" | "price_cents">): string {
  if (row.access === "free") return "Free";
  if (row.access === "members") return "Members";
  return new Intl.NumberFormat("en-US", { currency: row.currency.toUpperCase(), style: "currency" }).format(
    (row.price_cents ?? 0) / 100,
  );
}

function getBadge(row: BookRow, now: number): string | null {
  if (row.access === "free") return "Free";
  if (row.access === "members") return "Members";
  return isNewBook(row, now) ? "New" : null;
}

export function toProductCard(row: BookRow, now: number): ProductCard {
  const price = fromMinorUnits(row.access === "paid" ? (row.price_cents ?? 0) : 0, row.currency.toUpperCase());
  return {
    access: row.access,
    badge: getBadge(row, now),
    featuredImage: toCover(row),
    handle: row.slug,
    id: row.id,
    price,
    priceLabel: getPriceLabel(row),
    subtitle: row.subtitle ?? undefined,
    title: row.title,
    vendor: "Giggle & Snooze",
  };
}

export function toProductDetails(row: BookRow, now: number): ProductDetails {
  const card = toProductCard(row, now);
  const description = row.blurb ?? "";
  const cover = card.featuredImage;
  return {
    ...card,
    currencyCode: card.price.currencyCode,
    description,
    hasSample: Boolean(row.sample_epub_path),
    images: cover ? [cover] : [],
    memberIncluded: row.member_included,
    pageCount: row.page_count ?? undefined,
    seo: {
      description: summarizeText(description || `${row.title}, an illustrated bedtime story.`),
      title: row.subtitle ? `${row.title}: ${row.subtitle}` : row.title,
    },
    updatedAt: row.published_at ?? new Date(now).toISOString(),
    videos: [],
  };
}
