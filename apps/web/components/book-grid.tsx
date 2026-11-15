import Image from "next/image";
import Link from "next/link";

import { type Book, coverUrl, priceLabel } from "@/lib/books";

export function BookGrid({ books }: { books: Book[] }) {
  if (books.length === 0) return <p className="text-muted-foreground">New books are on the way.</p>;
  return (
    <ul className="grid grid-cols-2 gap-6 md:grid-cols-3">
      {books.map((book) => {
        const cover = coverUrl(book.cover_path);
        return (
          <li key={book.id}>
            <Link href={`/books/${book.slug}`} className="block space-y-2">
              <div className="relative aspect-square overflow-hidden rounded-xl bg-muted">
                {cover && <Image src={cover} alt={`Cover of ${book.title}`} fill sizes="(min-width: 768px) 33vw, 50vw" className="object-cover" />}
              </div>
              <div className="flex items-baseline justify-between gap-2">
                <h3 className="font-medium">{book.title}</h3>
                <span className="text-xs text-muted-foreground">{priceLabel(book)}</span>
              </div>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
