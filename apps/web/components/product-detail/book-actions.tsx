import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { ProductDetails } from "@/lib/product/types";
import { createAdminClient } from "@/lib/supabase/admin";
import { createClient } from "@/lib/supabase/server";

async function canReadFull(userId: string, bookId: string): Promise<boolean> {
  if (!process.env.SUPABASE_SERVICE_ROLE_KEY) return false;
  const { data } = await createAdminClient().rpc("can_read_book", {
    p_book: bookId,
    p_user: userId,
  });
  return data === true;
}

// Request-time: reads the session cookie, so it sits in its own Suspense boundary on the product page.
export async function BookActions({
  product,
}: {
  product: Pick<ProductDetails, "access" | "handle" | "hasSample" | "id" | "priceLabel">;
}) {
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();
  const user = data.user;
  const entitled = user ? await canReadFull(user.id, product.id) : false;
  const buyable = product.access === "paid" && !entitled;
  const readHref = `/api/books/${product.handle}/read`;

  return (
    <div data-slot="book-actions" className="flex flex-wrap gap-2.5">
      {entitled ? (
        <Button size="lg" className="h-12 min-w-40 flex-1" render={<a href={readHref} />}>
          {product.access === "free" ? "Read free" : "Read"}
        </Button>
      ) : null}
      {!entitled && user && product.hasSample ? (
        <Button size="lg" className="h-12 min-w-40 flex-1" render={<a href={readHref} />}>
          Read sample
        </Button>
      ) : null}
      {buyable && user ? (
        <form action="/api/checkout" method="post" className="flex min-w-40 flex-1">
          <input type="hidden" name="kind" value="book" />
          <input type="hidden" name="slug" value={product.handle} />
          <Button type="submit" variant="outline" size="lg" className="h-12 flex-1">
            Buy {product.priceLabel}
          </Button>
        </form>
      ) : null}
      {!user ? (
        <Button
          size="lg"
          variant="outline"
          className="h-12 min-w-40 flex-1"
          render={<a href="/account" />}
        >
          Sign in to read or buy
        </Button>
      ) : null}
    </div>
  );
}

export function BookActionsSkeleton() {
  return <Skeleton className="h-12 w-full" />;
}
