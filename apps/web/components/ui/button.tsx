import Link from "next/link";
import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonVariant = "primary" | "secondary" | "nav";

const variantClasses: Record<ButtonVariant, string> = {
  primary: "primary-button",
  secondary: "secondary-button",
  nav: "inline-flex items-center justify-center rounded-full bg-[#173f47] px-6 py-3 text-sm font-bold text-white shadow-[0_6px_0_#0d2d33] transition hover:-translate-y-0.5",
};

type BaseProps = {
  variant?: ButtonVariant;
  className?: string;
  children: ReactNode;
};

type ButtonProps = BaseProps & ButtonHTMLAttributes<HTMLButtonElement>;

export function Button({
  variant = "primary",
  className = "",
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      className={`${variantClasses[variant]} ${className}`.trim()}
      {...props}
    >
      {children}
    </button>
  );
}

type ButtonLinkProps = BaseProps & {
  href: string;
  external?: boolean;
} & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href">;

export function ButtonLink({
  href,
  variant = "primary",
  external = false,
  className = "",
  children,
  ...props
}: ButtonLinkProps) {
  const classes = `${variantClasses[variant]} ${className}`.trim();

  if (external) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className={classes}
        {...props}
      >
        {children}
      </a>
    );
  }

  return (
    <Link href={href} className={classes} {...props}>
      {children}
    </Link>
  );
}
