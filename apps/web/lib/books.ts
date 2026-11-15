import { createClient } from "@/lib/supabase/server";

export type Book = {
  id: string;
  slug: string;
  title: string;
  subtitle: string | null;
  blurb: string | null;
  access: "free" | "members" | "paid";
  member_included: boolean;
  price_cents: number | null;
  currency: string;
  stripe_price_id: string | null;
  page_count: number | null;
  cover_path: string | null;
};

const COLUMNS =
  "id, slug, title, subtitle, blurb, access, member_included, price_cents, currency, stripe_price_id, page_count, cover_path";

export async function listBooks(): Promise<Book[]> {
  const supabase = await createClient();
  const { data } = await supabase.from("books").select(COLUMNS).eq("published", true).order("published_at", { ascending: false });
  return (data ?? []) as Book[];
}

export async function getBook(slug: string): Promise<Book | null> {
  const supabase = await createClient();
  const { data } = await supabase.from("books").select(COLUMNS).eq("slug", slug).eq("published", true).maybeSingle();
  return (data as Book | null) ?? null;
}

export function coverUrl(path: string | null): string | null {
  if (!path) return null;
  return `${process.env.NEXT_PUBLIC_SUPABASE_URL}/storage/v1/object/public/covers/${path}`;
}

export function priceLabel(book: Book): string {
  if (book.access === "free") return "Free";
  if (book.access === "members") return "Members";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: book.currency }).format((book.price_cents ?? 0) / 100);
}
