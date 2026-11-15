import { BookGrid } from "@/components/book-grid";
import { SetupNote } from "@/components/setup-note";
import { Button } from "@/components/ui/button";
import { listBooks } from "@/lib/books";
import { site } from "@/lib/site";

export default async function Home() {
  const books = (await listBooks()).slice(0, 6);
  return (
    <div className="space-y-12">
      <SetupNote />
      <section className="space-y-4 rounded-3xl bg-secondary/60 px-6 py-12 md:px-12">
        <h1 className="text-4xl font-semibold md:text-5xl">{site.tagline}</h1>
        <p className="max-w-xl text-muted-foreground">
          Illustrated bedtime stories. Read some free, buy your favourites, or join for the whole shelf.
        </p>
        <div className="flex gap-3">
          <Button size="lg" render={<a href="/books" />}>Browse books</Button>
          <Button size="lg" variant="outline" render={<a href="/membership" />}>Become a member</Button>
        </div>
      </section>
      <BookGrid books={books} />
    </div>
  );
}
