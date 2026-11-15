import { supabaseConfigured } from "@/lib/books";

/** Dev-only hint when Supabase env vars are missing. */
export function SetupNote() {
  if (supabaseConfigured() || process.env.NODE_ENV === "production") return null;
  return (
    <p className="rounded-lg border border-dashed bg-accent px-4 py-3 text-sm text-muted-foreground">
      Set up Supabase: copy <code>.env.example</code> to <code>apps/web/.env.local</code> and fill in
      NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY (run <code>supabase start</code> locally).
    </p>
  );
}
