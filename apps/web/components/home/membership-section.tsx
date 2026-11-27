import { ButtonLink } from "@/components/ui/button";
import { ArrowIcon, SparkleIcon } from "@/components/ui/icons";

export function MembershipSection() {
  return (
    <section
      id="membership"
      className="mx-auto max-w-[1440px] px-5 py-24 sm:px-8 lg:px-12"
    >
      <div className="relative overflow-hidden rounded-[44px] bg-[#f7c9dc] px-7 py-14 sm:px-14 lg:grid lg:grid-cols-[1fr_.85fr] lg:items-center lg:px-20">
        <div className="relative z-10">
          <p className="eyebrow">Dreamer membership</p>
          <h2 className="mt-3 max-w-xl text-[clamp(2.5rem,5vw,4.8rem)] font-black leading-[.95] tracking-[-.055em] text-[#173f47]">
            A whole library in their little hands.
          </h2>
          <p className="mt-6 max-w-lg text-lg font-semibold leading-8 text-[#4d686d]">
            Unlimited stories, audio companions, and calm bedtime favorites.
            Start free and upgrade whenever your little reader is ready.
          </p>
          <ButtonLink href="/signup" variant="primary" className="mt-8">
            Create a free account <ArrowIcon className="size-5" />
          </ButtonLink>
        </div>

        <div className="relative mt-12 min-h-[300px] lg:mt-0" aria-hidden="true">
          <div className="absolute left-[8%] top-12 h-64 w-44 -rotate-12 rounded-[16px] bg-[#ffd664] p-5 shadow-xl">
            <p className="text-xs font-black uppercase tracking-widest text-[#173f47]/60">
              Bedtime tales
            </p>
            <p className="mt-6 text-3xl font-black leading-none text-[#173f47]">
              Moonlight stories
            </p>
          </div>
          <div className="absolute right-[10%] top-0 h-72 w-48 rotate-6 rounded-[16px] bg-[#9dded6] p-5 shadow-xl">
            <SparkleIcon className="size-8 text-[#173f47]" />
            <p className="mt-20 text-3xl font-black leading-none text-[#173f47]">
              Wonder waits inside
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
