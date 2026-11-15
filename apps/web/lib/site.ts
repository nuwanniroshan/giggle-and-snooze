export const site = {
  name: "Giggle & Snooze",
  url: process.env.NEXT_PUBLIC_SITE_URL ?? "https://giggleandsnooze.com",
  readerUrl: process.env.NEXT_PUBLIC_READER_URL ?? "https://read.giggleandsnooze.com",
  tagline: "Giggly stories for sleepy heads.",
} as const;
