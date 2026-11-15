import { site } from "@/lib/site";

import type { ShopConfig } from "./types";

export const shopConfig = {
  localization: {
    country: "US",
    language: "EN",
    locale: "en-US" as const,
  },
  search: {
    isEnabled: true,
  },
  site: {
    description: "Illustrated bedtime stories to read, giggle at and fall asleep to.",
    name: site.name,
    tagline: site.tagline,
    url: site.url,
  },
} satisfies ShopConfig;
