import Image from "next/image";
import { notFound } from "next/navigation";

import { SetupNote } from "@/components/setup-note";
import { Button } from "@/components/ui/button";
import { coverUrl, getBook, priceLabel } from "@/lib/books";
import { createAdminClient } from "@/lib/supabase/admin";
import { createClient } from "@/lib/supabase/server";

async function canReadFull(userId: string, bookId: string): Promise<boolean> {
  if (!process.env.SUPABASE_SERVICE_ROLE_KEY) return false;
  const { data } = await createAdminClient().rpc("can_read_book", { p_user: userId, p_book: bookId });
  return data === true;
}

export default async function BookPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const book = await getBook(slug);
  if (!book) {
    if (!process.env.NEXT_PUBLIC_SUPABASE_URL) return <SetupNote />;
    notFound();
  }
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();
  const user = data.user;
  const entitled = user ? await canReadFull(user.id, book.id) : false;
  const cover = coverUrl(book.cover_path);
  const buyable = book.access === "paid" && !entitled;
  const readHref = `/api/books/${book.slug}/read`;

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
        {book.member_included && book.access !== "free" && (
          <p className="rounded-lg bg-secondary/60 px-3 py-2 text-sm">Members read this too.</p>
        )}
        <div className="flex flex-wrap gap-3">
          {entitled && <Button render={<a href={readHref} />}>{book.access === "free" ? "Read free" : "Read"}</Button>}
          {!entitled && user && book.has_sample && <Button render={<a href={readHref} />}>Read sample</Button>}
          {buyable && user && (
            <form action="/api/checkout" method="post">
              <input type="hidden" name="kind" value="book" />
              <input type="hidden" name="slug" value={book.slug} />
              <Button type="submit" variant="outline">Buy {priceLabel(book)}</Button>
            </form>
          )}
          {!user && <Button variant="outline" render={<a href="/account" />}>Sign in to read or buy</Button>}
        </div>
      </div>
    </article>
  );
}
