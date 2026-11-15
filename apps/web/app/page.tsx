import Link from "next/link";

import { BookGrid } from "@/components/book-grid";
import { listBooks } from "@/lib/books";
import { site } from "@/lib/site";

export default async function Home() {
  const books = (await listBooks()).slice(0, 6);
  return (
    <div className="space-y-12">
      <section className="space-y-4 py-8">
        <h1 className="text-5xl font-semibold">{site.tagline}</h1>
        <p className="max-w-xl text-muted-foreground">Illustrated bedtime stories. Read some free, buy your favourites, or join for the whole shelf.</p>
        <div className="flex gap-3">
          <Link href="/books" className="rounded-md bg-primary px-5 py-2 text-sm text-primary-foreground">Browse books</Link>
          <Link href="/membership" className="rounded-md border px-5 py-2 text-sm">Become a member</Link>
        </div>
      </section>
      <BookGrid books={books} />
    </div>
  );
}
