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
  has_sample: boolean;
};

type BookRow = Omit<Book, "has_sample"> & { sample_epub_path: string | null };

/** True when Supabase is configured; pages show a setup note otherwise (dev). */
export function supabaseConfigured(): boolean {
  return Boolean(process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY);
}

function toBook(row: BookRow): Book {
  const { sample_epub_path, ...rest } = row;
  return { ...rest, has_sample: Boolean(sample_epub_path) };
}

const COLUMNS =
  "id, slug, title, subtitle, blurb, access, member_included, price_cents, currency, stripe_price_id, page_count, cover_path, sample_epub_path";

export async function listBooks(): Promise<Book[]> {
  if (!supabaseConfigured()) return [];
  const supabase = await createClient();
  const { data } = await supabase.from("books").select(COLUMNS).eq("published", true).order("published_at", { ascending: false });
  return ((data ?? []) as BookRow[]).map(toBook);
}

export async function getBook(slug: string): Promise<Book | null> {
  if (!supabaseConfigured()) return null;
  const supabase = await createClient();
  const { data } = await supabase.from("books").select(COLUMNS).eq("slug", slug).eq("published", true).maybeSingle();
  return data ? toBook(data as BookRow) : null;
}

export function coverUrl(path: string | null): string | null {
  if (!path || !process.env.NEXT_PUBLIC_SUPABASE_URL) return null;
  return `${process.env.NEXT_PUBLIC_SUPABASE_URL}/storage/v1/object/public/covers/${path}`;
}

export function priceLabel(book: Book): string {
  if (book.access === "free") return "Free";
  if (book.access === "members") return "Members";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: book.currency }).format((book.price_cents ?? 0) / 100);
}
