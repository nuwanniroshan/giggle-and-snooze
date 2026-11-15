import { shopConfig } from "@/lib/config";
import type { Image } from "@/lib/media/types";

interface ProductSchemaData {
  currencyCode: string;
  description: string;
  handle: string;
  images: Image[];
  price: string;
  title: string;
  vendor?: string;
}

export function ProductSchema({ product }: { product: ProductSchemaData }) {
  const url = `${shopConfig.site.url}/products/${product.handle}`;
  const schema = {
    "@context": "https://schema.org",
    "@type": "Book",
    name: product.title,
    description: product.description,
    image: product.images.map((img) => img.url),
    author: product.vendor ? { "@type": "Organization", name: product.vendor } : undefined,
    url,
    offers: {
      "@type": "Offer",
      availability: "https://schema.org/InStock",
      price: product.price,
      priceCurrency: product.currencyCode,
      url,
    },
  };

  return <script type="application/ld+json">{JSON.stringify(schema)}</script>;
}
