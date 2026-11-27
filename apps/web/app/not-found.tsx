import { SiteFooter } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";
import { ButtonLink } from "@/components/ui/button";
import { BookIcon } from "@/components/ui/icons";

export default function NotFound() {
  return (
    <main className="flex min-h-screen flex-col bg-[#fffdf8]">
      <SiteHeader />
      <section className="mx-auto flex max-w-2xl flex-1 flex-col items-center justify-center px-8 py-20 text-center">
        <div className="grid size-16 place-items-center rounded-full bg-[#dff5f2] text-[#173f47]">
          <BookIcon className="size-8" />
        </div>
        <p className="eyebrow mt-6">Lost in the bookshelf</p>
        <h1 className="mt-2 text-4xl font-black tracking-[-.05em] text-[#173f47] sm:text-5xl">
          Page not found
        </h1>
        <p className="mt-4 font-semibold leading-7 text-[#61777b]">
          The story page you&apos;re looking for doesn&apos;t exist or has been
          tucked onto another shelf.
        </p>
        <ButtonLink href="/" variant="primary" className="mt-8">
          ← Back to Home
        </ButtonLink>
      </section>
      <SiteFooter />
    </main>
  );
}
