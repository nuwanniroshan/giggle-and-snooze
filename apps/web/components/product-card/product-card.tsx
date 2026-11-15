import { Link } from "@/components/ui/link";
import { buildProductUrl } from "@/lib/product";
import type { ProductCard as ProductCardType } from "@/lib/product/types";

import {
  ProductCardBadge,
  ProductCardContent,
  ProductCardImage,
  ProductCardImageContainer,
  ProductCardPrice,
  ProductCard as ProductCardRoot,
  ProductCardSkeleton,
  ProductCardTitle,
} from "./components";

export interface ProductCardProps {
  loading?: "eager" | "lazy";
  product: ProductCardType;
}

export function ProductCard({ loading, product }: ProductCardProps) {
  const href = buildProductUrl(product.handle);
  return (
    <ProductCardRoot>
      <ProductCardImageContainer>
        <ProductCardImage
          src={product.featuredImage?.url}
          alt={product.featuredImage?.altText || product.title}
          height={product.featuredImage?.height}
          loading={loading}
          width={product.featuredImage?.width}
        >
          {product.badge ? <ProductCardBadge>{product.badge}</ProductCardBadge> : null}
        </ProductCardImage>
        <ProductCardContent>
          <ProductCardTitle>
            <Link href={href} className="after:absolute after:inset-0">
              {product.title}
            </Link>
          </ProductCardTitle>
          <ProductCardPrice
            access={product.access}
            amount={product.price.amount}
            currencyCode={product.price.currencyCode}
            label={product.priceLabel}
          />
        </ProductCardContent>
      </ProductCardImageContainer>
    </ProductCardRoot>
  );
}

export { ProductCardSkeleton };
