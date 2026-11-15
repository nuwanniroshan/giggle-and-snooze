import { cn } from "cn";
import type { ComponentProps } from "react";

import { Price } from "@/components/product/price";
import type { BookAccess } from "@/lib/product/types";

interface ProductPriceProps extends ComponentProps<"div"> {
  access: BookAccess;
  amount: string;
  currencyCode: string;
  label: string;
}

export function ProductPrice({
  access,
  amount,
  className,
  currencyCode,
  label,
  ...props
}: ProductPriceProps) {
  return (
    <div className={cn("flex items-center gap-2.5 flex-wrap", className)} {...props}>
      {access === "paid" ? (
        <Price amount={amount} currencyCode={currencyCode} className="text-xl" />
      ) : (
        <span className="font-mono text-xl tracking-tight text-foreground">{label}</span>
      )}
    </div>
  );
}
