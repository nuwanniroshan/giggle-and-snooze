import type { ReactNode } from "react";

export function StoryPill({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <span className={`story-pill ${className}`.trim()}>{children}</span>;
}

export function CheckFeature({ children }: { children: ReactNode }) {
  return (
    <span className="flex items-center gap-2">
      <span className="check-dot" aria-hidden="true">
        ✓
      </span>
      {children}
    </span>
  );
}
