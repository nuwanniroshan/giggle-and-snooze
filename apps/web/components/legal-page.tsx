import { Prose } from "@/components/ui/prose";

export function LegalPage({ title, updated, children }: { title: string; updated: string; children: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <p className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
        DRAFT: this text is a plain-language placeholder and has not been reviewed by a lawyer. It must be
        reviewed before launch.
      </p>
      <h1 className="text-4xl font-semibold">{title}</h1>
      <p className="text-sm text-muted-foreground">Last updated: {updated}</p>
      <Prose className="max-w-none">{children}</Prose>
    </div>
  );
}
