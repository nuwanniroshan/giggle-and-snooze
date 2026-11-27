"use client";

import Image from "next/image";
import Link from "next/link";

import { useDemoReader } from "@/lib/demo-session";

export function SiteFooter() {
  const { reader, hydrated } = useDemoReader();

  return (
    <footer className="border-t border-[#173f47]/10 bg-[#fffdf8]">
      <div className="mx-auto flex max-w-[1440px] flex-col items-center justify-between gap-6 px-6 py-10 text-center sm:flex-row sm:text-left">
        <Link href="/" aria-label="Giggle and Snooze home">
          <Image
            src="/assets/giggle-snooze-logo.png"
            alt="Giggle and Snooze"
            width={145}
            height={85}
            className="h-16 w-auto object-contain"
          />
        </Link>
        <p className="text-sm font-semibold text-[#708387]">
          Small stories. Big imaginations. Better bedtimes.
        </p>
        <div className="flex gap-6 text-sm font-black text-[#173f47]">
          <Link href="/books" className="transition hover:text-[#ef6d4f]">
            Books
          </Link>
          <Link
            href={hydrated && reader ? "/account" : "/login"}
            className="transition hover:text-[#ef6d4f]"
          >
            {hydrated && reader ? "Account" : "Log in"}
          </Link>
        </div>
      </div>
    </footer>
  );
}
