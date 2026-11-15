export interface CollectionDefinition {
  description: string;
  handle: "free" | "members" | "new" | "paid";
  title: string;
}

// Collections are derived from each book's access level and publish date; nothing is stored.
export const COLLECTION_DEFINITIONS: readonly CollectionDefinition[] = [
  {
    description: "New stories, hot off the press.",
    handle: "new",
    title: "New",
  },
  {
    description: "Free to read, always. Sign in and settle in.",
    handle: "free",
    title: "Free books",
  },
  {
    description: "Included with a Giggle & Snooze membership.",
    handle: "members",
    title: "Members",
  },
  {
    description: "Own it forever with a one-time purchase.",
    handle: "paid",
    title: "Paid books",
  },
];
