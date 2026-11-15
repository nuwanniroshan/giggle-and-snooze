import type { Money } from "@/lib/money/types";

// Next Metadata cannot emit product `property=` tags, so React hoists these raw meta elements.
export function ProductOpenGraph({ price }: { price: Money }) {
  return (
    <>
      <meta property="og:type" content="product" />
      <meta property="og:price:amount" content={price.amount} />
      <meta property="og:price:currency" content={price.currencyCode} />
      <meta property="og:availability" content="instock" />
      <meta property="product:price:amount" content={price.amount} />
      <meta property="product:price:currency" content={price.currencyCode} />
      <meta property="product:availability" content="in stock" />
    </>
  );
}
