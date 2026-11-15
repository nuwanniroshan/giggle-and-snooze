"use client";

import { useRouter, useSearchParams } from "next/navigation";

import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select";
import { SORT_VALUES } from "@/lib/collections";
import type { SortValue } from "@/lib/collections/types";

const SORT_LABELS: Record<SortValue, string> = {
  manual: "Newest",
  "price-ascending": "Price: Low to High",
  "price-descending": "Price: High to Low",
  "title-ascending": "Name: A-Z",
  "title-descending": "Name: Z-A",
};

export function CollectionsSortSelect({
  exclude = [],
  path,
}: {
  exclude?: SortValue[];
  path: string;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const raw = searchParams.get("sort");
  const current = SORT_VALUES.find((value) => value === raw) ?? "manual";
  const options = SORT_VALUES.filter((value) => !exclude.includes(value));
  return (
    <Select
      value={current}
      onValueChange={(value) => {
        const next = new URLSearchParams(searchParams.toString());
        if (!value || value === "manual") next.delete("sort");
        else next.set("sort", value);
        const search = next.toString();
        router.push(search ? `${path}?${search}` : path, { scroll: false });
      }}
    >
      <SelectTrigger className="border-0 shadow-none bg-transparent px-0">
        <span>Sort</span>
      </SelectTrigger>
      <SelectContent>
        {options.map((value) => (
          <SelectItem key={value} value={value}>
            {SORT_LABELS[value]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
