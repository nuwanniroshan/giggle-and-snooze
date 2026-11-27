import Link from "next/link";
import type { ReactNode } from "react";

type AuthCardProps = {
  eyebrow: string;
  title: string;
  description: string;
  footerPrompt: string;
  footerLinkLabel: string;
  footerLinkHref: string;
  children: ReactNode;
};

export function AuthCard({
  eyebrow,
  title,
  description,
  footerPrompt,
  footerLinkLabel,
  footerLinkHref,
  children,
}: AuthCardProps) {
  return (
    <section className="mx-auto w-full max-w-md flex-1 px-5 py-16">
      <div className="rounded-[36px] bg-white p-7 shadow-[0_22px_70px_rgba(23,63,71,.12)] sm:p-10">
        <p className="eyebrow">{eyebrow}</p>
        <h1 className="mt-2 text-4xl font-black tracking-[-.05em] text-[#173f47]">
          {title}
        </h1>
        <p className="mb-8 mt-3 font-semibold leading-7 text-[#61777b]">
          {description}
        </p>
        {children}
        <p className="mt-7 text-center text-sm font-semibold text-[#61777b]">
          {footerPrompt}{" "}
          <Link
            className="font-black text-[#173f47] underline decoration-[#ffd664] decoration-4"
            href={footerLinkHref}
          >
            {footerLinkLabel}
          </Link>
        </p>
      </div>
    </section>
  );
}
