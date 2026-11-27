import type { ReactNode } from "react";

type SectionHeadingProps = {
  eyebrow: string;
  title: ReactNode;
  description?: ReactNode;
  align?: "left" | "center";
  as?: "h1" | "h2";
  titleClassName?: string;
  className?: string;
};

export function SectionHeading({
  eyebrow,
  title,
  description,
  align = "left",
  as: Heading = "h2",
  titleClassName,
  className = "",
}: SectionHeadingProps) {
  const isCentered = align === "center";
  const defaultTitleClass =
    Heading === "h1"
      ? "mt-2 text-[clamp(3rem,7vw,6rem)] font-black leading-[.92] tracking-[-.065em] text-[#173f47]"
      : "section-title";

  return (
    <div
      className={`${isCentered ? "mx-auto text-center" : ""} ${className}`.trim()}
    >
      <p className="eyebrow">{eyebrow}</p>
      <Heading className={titleClassName ?? defaultTitleClass}>{title}</Heading>
      {description && (
        <p
          className={`mt-6 text-lg font-semibold leading-8 text-[#61777b] ${
            isCentered ? "mx-auto max-w-2xl" : "max-w-xl"
          }`}
        >
          {description}
        </p>
      )}
    </div>
  );
}
