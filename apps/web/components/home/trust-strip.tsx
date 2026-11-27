import { Fragment } from "react";

const TRUST_HIGHLIGHTS = [
  "Safe & ad-free reading",
  "New stories monthly",
  "Read anywhere",
] as const;

export function TrustStrip() {
  return (
    <section className="bg-[#173f47] py-6 text-white">
      <div className="mx-auto flex max-w-[1440px] flex-wrap items-center justify-center gap-x-12 gap-y-4 px-6 text-sm font-black tracking-wide">
        {TRUST_HIGHLIGHTS.map((item, idx) => (
          <Fragment key={item}>
            {idx > 0 && (
              <span className="text-[#ffd664]" aria-hidden="true">
                ✦
              </span>
            )}
            <span>{item}</span>
          </Fragment>
        ))}
      </div>
    </section>
  );
}
