import Image from "next/image";
import Link from "next/link";

import { CheckFeature } from "@/components/ui/badges";
import { ButtonLink } from "@/components/ui/button";
import {
  ArrowIcon,
  HeadphonesIcon,
  PlayIcon,
  SparkleIcon,
  StarIcon,
} from "@/components/ui/icons";
import type { BookView } from "@/lib/books";

export function HeroSection({ featured }: { featured?: BookView }) {
  const featuredHref = featured ? `/books/${featured.slug}` : "/books";
  const featuredTitle = featured?.title ?? "Room on the Quilt";

  return (
    <section className="relative">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_80%_20%,#dff5f2_0,transparent_34%),radial-gradient(circle_at_14%_75%,#fff0bd_0,transparent_28%)]" />
      <div className="relative mx-auto grid min-h-[680px] max-w-[1440px] items-center gap-12 px-5 py-16 sm:px-8 lg:grid-cols-[.9fr_1.1fr] lg:px-12 lg:py-20">
        <div className="relative z-10">
          <div className="mb-7 inline-flex items-center gap-2 rounded-full border border-[#173f47]/10 bg-white/80 px-4 py-2 text-xs font-black uppercase tracking-[.15em] text-[#173f47] shadow-sm">
            <SparkleIcon className="size-4 text-[#f4ad3d]" /> Stories for little
            dreamers
          </div>

          <h1 className="max-w-[680px] text-[clamp(3.5rem,7vw,6.8rem)] font-black leading-[.88] tracking-[-.07em] text-[#173f47]">
            Big stories for{" "}
            <span className="relative inline-block text-[#ef6d4f]">
              little minds
              <span className="absolute -bottom-2 left-1 h-2 w-[95%] -rotate-1 rounded-full bg-[#ffd664]" />
            </span>
          </h1>

          <p className="mt-8 max-w-xl text-lg font-semibold leading-8 text-[#587176]">
            A joyful digital bookshelf where curious kids can read, listen, and
            wind down with stories made to spark wonder.
          </p>

          <div className="mt-9 flex flex-wrap gap-4">
            <ButtonLink href="/books" variant="primary">
              Explore the bookshelf <ArrowIcon className="size-5" />
            </ButtonLink>
            <ButtonLink href={featuredHref} variant="secondary">
              <PlayIcon className="size-5" /> Read today&apos;s pick
            </ButtonLink>
          </div>

          <div className="mt-9 flex flex-wrap gap-x-7 gap-y-3 text-sm font-bold text-[#5e7478]">
            <CheckFeature>Free stories every week</CheckFeature>
            <CheckFeature>PDF + ePUB</CheckFeature>
            <CheckFeature>Ages 0–12</CheckFeature>
          </div>
        </div>

        <div className="relative mx-auto w-full max-w-[720px]">
          <div className="absolute -left-5 top-12 z-10 rounded-[22px] bg-white p-4 shadow-[0_15px_45px_rgba(23,63,71,.15)] sm:-left-9">
            <StarIcon className="size-7 fill-[#ffd664] text-[#eaa62b]" />
            <p className="mt-1 text-xs font-black text-[#173f47]">Kid-loved</p>
          </div>

          <div className="relative aspect-[1.12/1] overflow-hidden rounded-[44px_44px_120px_44px] bg-[#dff5f2] p-3 shadow-[0_30px_80px_rgba(23,63,71,.18)]">
            <Image
              src="/assets/story-page.png"
              alt="A child sitting on a quilt under a pear tree"
              fill
              sizes="(max-width: 1024px) 92vw, 52vw"
              className="object-cover"
              priority
            />
            <div className="absolute inset-x-6 bottom-6 flex items-center justify-between gap-4 rounded-[22px] bg-[#fffdf8]/92 p-4 shadow-lg backdrop-blur-md">
              <div>
                <p className="text-[11px] font-black uppercase tracking-[.16em] text-[#ef6d4f]">
                  Today&apos;s cozy pick
                </p>
                <p className="mt-1 text-base font-black text-[#173f47]">
                  {featuredTitle}
                </p>
              </div>
              <Link
                href={featuredHref}
                className="grid size-12 shrink-0 place-items-center rounded-full bg-[#ffd664] text-[#173f47] transition hover:scale-105"
                aria-label="Open today's cozy pick"
              >
                <ArrowIcon className="size-5" />
              </Link>
            </div>
          </div>

          <div className="absolute -bottom-6 -right-4 z-10 flex items-center gap-3 rounded-[22px] bg-[#173f47] p-4 pr-6 text-white shadow-xl sm:right-2">
            <HeadphonesIcon className="size-7 text-[#ffd664]" />
            <div>
              <p className="text-xs font-bold text-white/65">Listen along</p>
              <p className="text-sm font-black">Storytime audio</p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
