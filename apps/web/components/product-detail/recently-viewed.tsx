"use client";

import { useEffect } from "react";

import { recordRecentlyViewedProduct } from "@/lib/product/client";
import type { RecentlyViewedProduct } from "@/lib/product/types";

export function RecentlyViewedRecorder({ product }: { product: RecentlyViewedProduct }) {
  const { featuredImage, handle, id, price, priceLabel, title } = product;
  useEffect(() => {
    recordRecentlyViewedProduct({ featuredImage, handle, id, price, priceLabel, title });
  }, [featuredImage, handle, id, price, priceLabel, title]);
  return null;
}
