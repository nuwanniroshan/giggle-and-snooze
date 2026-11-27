import Link from "next/link";

import { BookGrid, EmptyShelf } from "@/components/books/book-grid";
import { ArrowIcon } from "@/components/ui/icons";
import { SectionHeading } from "@/components/ui/section-heading";
import type { BookView } from "@/lib/books";

export function FeaturedBooksSection({ books }: { books: BookView[] }) {
  return (
    <section className="mx-auto max-w-[1440px] px-5 py-24 sm:px-8 lg:px-12">
      <div className="flex flex-col justify-between gap-6 sm:flex-row sm:items-end">
        <SectionHeading
          eyebrow="Fresh from the bookshelf"
          title="Stories kids ask for again"
        />
        <Link
          href="/books"
          className="inline-flex items-center gap-2 font-black text-[#173f47] transition hover:text-[#ef6d4f]"
        >
          See all stories <ArrowIcon className="size-5" />
        </Link>
      </div>

      {books.length > 0 ? (
        <BookGrid books={books.slice(0, 4)} />
      ) : (
        <EmptyShelf />
      )}
    </section>
  );
}
