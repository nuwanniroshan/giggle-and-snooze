import Image from "next/image";
import { notFound } from "next/navigation";

import { coverUrl, getBook, priceLabel } from "@/lib/books";
import { createClient } from "@/lib/supabase/server";

export default async function BookPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const book = await getBook(slug);
  if (!book) notFound();
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();
  const cover = coverUrl(book.cover_path);
  const buyable = book.access === "paid";

  return (
    <article className="grid gap-8 md:grid-cols-2">
      <div className="relative aspect-square overflow-hidden rounded-xl bg-muted">
        {cover && <Image src={cover} alt={`Cover of ${book.title}`} fill priority sizes="(min-width: 768px) 50vw, 100vw" className="object-cover" />}
      </div>
      <div className="space-y-5">
        <h1 className="text-4xl font-semibold">{book.title}</h1>
        {book.subtitle && <p className="text-muted-foreground">{book.subtitle}</p>}
        {book.blurb && <p>{book.blurb}</p>}
        <p className="text-sm text-muted-foreground">{priceLabel(book)}{book.page_count ? ` · ${book.page_count} pages` : ""}</p>
        <div className="flex flex-wrap gap-3">
          <a href={`/api/books/${book.slug}/read`} className="rounded-md bg-primary px-5 py-2 text-sm text-primary-foreground">
            {book.access === "free" ? "Read free" : "Read"}
          </a>
          {buyable && data.user && (
            <form action="/api/checkout" method="post">
              <input type="hidden" name="kind" value="book" />
              <input type="hidden" name="slug" value={book.slug} />
              <button className="rounded-md border px-5 py-2 text-sm">Buy {priceLabel(book)}</button>
            </form>
          )}
          {!data.user && <a href="/account" className="rounded-md border px-5 py-2 text-sm">Sign in to read or buy</a>}
        </div>
      </div>
    </article>
  );
}
