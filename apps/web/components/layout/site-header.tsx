"use client";

import Image from "next/image";
import Link from "next/link";
import { useState } from "react";

import { ButtonLink } from "@/components/ui/button";
import { CloseIcon, MenuIcon, SearchIcon } from "@/components/ui/icons";
import { useDemoReader } from "@/lib/demo-session";

const NAV_ITEMS = [
  { label: "Explore books", href: "/books" },
  { label: "How it works", href: "/#how-it-works" },
  { label: "Membership", href: "/#membership" },
] as const;

export function SiteHeader() {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const { reader, hydrated } = useDemoReader();

  return (
    <header className="relative z-30 border-b border-[#143f47]/10 bg-[#fffdf8]/90 backdrop-blur-md">
      <div className="mx-auto flex h-[86px] max-w-[1440px] items-center gap-4 px-5 sm:gap-7 sm:px-8 lg:px-12">
        <Link
          href="/"
          className="shrink-0"
          aria-label="Giggle and Snooze home"
        >
          <Image
            src="/assets/giggle-snooze-logo.png"
            alt="Giggle and Snooze"
            width={150}
            height={88}
            className="h-[62px] w-auto object-contain"
            priority
          />
        </Link>

        <nav
          className="ml-auto hidden items-center gap-8 text-[15px] font-bold text-[#173f47] md:flex"
          aria-label="Main navigation"
        >
          {NAV_ITEMS.map((item) => (
            <Link key={item.href} className="nav-link" href={item.href}>
              {item.label}
            </Link>
          ))}
        </nav>

        <Link
          href="/books"
          className="ml-auto grid size-11 place-items-center rounded-full border border-[#173f47]/15 text-[#173f47] transition hover:bg-[#e9f8f6] md:ml-0"
          aria-label="Search books"
        >
          <SearchIcon className="size-5" />
        </Link>

        {hydrated && reader ? (
          <ButtonLink
            href="/account"
            variant="nav"
            className="hidden sm:inline-flex"
          >
            Hi, {reader.displayName}
          </ButtonLink>
        ) : (
          <ButtonLink
            href="/login"
            variant="nav"
            className="hidden sm:inline-flex"
          >
            Log in
          </ButtonLink>
        )}

        <button
          type="button"
          onClick={() => setMobileMenuOpen((prev) => !prev)}
          className="grid size-11 place-items-center rounded-full border border-[#173f47]/15 text-[#173f47] transition hover:bg-[#e9f8f6] md:hidden"
          aria-expanded={mobileMenuOpen}
          aria-label={mobileMenuOpen ? "Close menu" : "Open menu"}
        >
          {mobileMenuOpen ? (
            <CloseIcon className="size-5" />
          ) : (
            <MenuIcon className="size-5" />
          )}
        </button>
      </div>

      {mobileMenuOpen && (
        <nav
          aria-label="Mobile navigation"
          className="border-t border-[#143f47]/10 bg-[#fffdf8] px-5 py-5 shadow-lg md:hidden"
        >
          <div className="flex flex-col gap-3 text-base font-bold text-[#173f47]">
            {NAV_ITEMS.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => setMobileMenuOpen(false)}
                className="rounded-2xl px-3 py-2.5 transition hover:bg-[#eef8f6]"
              >
                {item.label}
              </Link>
            ))}
            <div className="pt-2 sm:hidden">
              <ButtonLink
                href={hydrated && reader ? "/account" : "/login"}
                variant="nav"
                className="w-full"
                onClick={() => setMobileMenuOpen(false)}
              >
                {hydrated && reader ? `Hi, ${reader.displayName}` : "Log in"}
              </ButtonLink>
            </div>
          </div>
        </nav>
      )}
    </header>
  );
}
