import { cn } from "cn";
import Image from "next/image";
import type { ComponentProps, ReactNode } from "react";

import { Price } from "@/components/product/price";
import { ImagePlaceholder } from "@/components/ui/image-placeholder";
import type { BookAccess } from "@/lib/product/types";

function ProductCard({ className, children, ...props }: ComponentProps<"article">) {
  return (
    <article
      data-slot="product-card"
      className={cn("relative flex flex-col h-full overflow-hidden", className)}
      {...props}
    >
      {children}
    </article>
  );
}

function ProductCardImageContainer({ className, children, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="product-card-image-container"
      className={cn("flex flex-col", className)}
      {...props}
    >
      {children}
    </div>
  );
}

interface ProductCardImageProps {
  alt: string;
  children?: ReactNode;
  className?: string;
  height?: number;
  loading?: "eager" | "lazy";
  src?: string | null;
  width?: number;
}

function ProductCardImage({
  alt,
  children,
  className,
  height,
  loading = "lazy",
  src,
  width,
}: ProductCardImageProps) {
  return (
    <div
      data-slot="product-card-image"
      className={cn("relative aspect-square overflow-hidden", className)}
    >
      {src ? (
        <Image
          src={src}
          alt={alt}
          {...(width && height ? { height, width } : { fill: true })}
          className="absolute inset-0 size-full object-cover"
          loading={loading}
          sizes="100vw"
        />
      ) : (
        <ImagePlaceholder className="size-full" />
      )}
      {children}
    </div>
  );
}

function ProductCardContent({ className, children, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="product-card-content"
      className={cn("flex flex-col flex-1 py-2.5", className)}
      {...props}
    >
      {children}
    </div>
  );
}

function ProductCardTitle({ className, children, ...props }: ComponentProps<"h3">) {
  return (
    <h3
      data-slot="product-card-title"
      className={cn("text-sm font-medium text-foreground line-clamp-1", className)}
      {...props}
    >
      {children}
    </h3>
  );
}

interface ProductCardPriceProps {
  access: BookAccess;
  amount: string;
  className?: string;
  currencyCode: string;
  label: string;
}

function ProductCardPrice({ access, amount, className, currencyCode, label }: ProductCardPriceProps) {
  return (
    <div data-slot="product-card-price" className={cn(className)}>
      {access === "paid" ? (
        <Price amount={amount} currencyCode={currencyCode} className="text-sm text-foreground" />
      ) : (
        <span className="font-mono text-sm tracking-tight text-foreground">{label}</span>
      )}
    </div>
  );
}

function ProductCardBadge({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      data-slot="product-card-badge"
      className={cn(
        "absolute left-2.5 top-2.5 rounded-full bg-background/90 px-2.5 py-1 text-xs font-medium text-foreground backdrop-blur-sm",
        className,
      )}
    >
      {children}
    </span>
  );
}

function ProductCardSkeleton({ className }: { className?: string }) {
  return (
    <div
      data-slot="product-card-skeleton"
      className={cn("flex flex-col overflow-hidden", className)}
    >
      <ImagePlaceholder className="aspect-square animate-pulse" />
      <div className="py-2.5 h-12 box-content grid gap-2">
        <div className="h-4 w-full bg-accent animate-pulse" />
        <div className="h-4 w-12 bg-accent animate-pulse" />
      </div>
    </div>
  );
}

export {
  ProductCard,
  ProductCardBadge,
  ProductCardContent,
  ProductCardImage,
  ProductCardImageContainer,
  ProductCardPrice,
  ProductCardSkeleton,
  ProductCardTitle,
};
