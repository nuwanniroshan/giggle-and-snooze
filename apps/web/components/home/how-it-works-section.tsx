import { SectionHeading } from "@/components/ui/section-heading";

type Step = {
  number: string;
  title: string;
  copy: string;
};

const STEPS: Step[] = [
  {
    number: "01",
    title: "Find their next favorite",
    copy: "Search by age, mood, or how much time you have.",
  },
  {
    number: "02",
    title: "Pick how to enjoy it",
    copy: "Open a beautiful PDF or ePUB, or listen along on video.",
  },
  {
    number: "03",
    title: "Make bedtime brighter",
    copy: "Save favorites and return to a cozy reading routine.",
  },
];

export function StepCard({ step }: { step: Step }) {
  return (
    <article className="rounded-[32px] border border-[#173f47]/8 bg-[#fffdf8] p-8 transition hover:-translate-y-1">
      <span className="text-sm font-black text-[#ef6d4f]">{step.number}</span>
      <h3 className="mt-8 text-2xl font-black tracking-[-.03em] text-[#173f47]">
        {step.title}
      </h3>
      <p className="mt-4 font-semibold leading-7 text-[#61777b]">{step.copy}</p>
    </article>
  );
}

export function HowItWorksSection() {
  return (
    <section id="how-it-works" className="bg-[#f1f6e8] py-24">
      <div className="mx-auto max-w-[1440px] px-5 sm:px-8 lg:px-12">
        <SectionHeading
          eyebrow="Simple by design"
          title="From “I’m bored” to story time"
          align="center"
          className="max-w-2xl"
        />

        <div className="mt-14 grid gap-5 md:grid-cols-3">
          {STEPS.map((step) => (
            <StepCard key={step.number} step={step} />
          ))}
        </div>
      </div>
    </section>
  );
}
