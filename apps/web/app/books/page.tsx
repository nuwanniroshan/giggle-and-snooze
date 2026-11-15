import { BookGrid } from "@/components/book-grid";
import { listBooks } from "@/lib/books";

export const metadata = { title: "Books" };

export default async function Books() {
  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-semibold">Books</h1>
      <BookGrid books={await listBooks()} />
    </div>
  );
}
