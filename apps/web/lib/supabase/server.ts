import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

/** Supabase client bound to the request's session (RLS applies). */
export async function createClient() {
  const store = await cookies();
  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll: () => store.getAll(),
        setAll: (items) => {
          try {
            for (const { name, value, options } of items) store.set(name, value, options);
          } catch {
            // Called from a Server Component: proxy.ts refreshes the session instead.
          }
        },
      },
    },
  );
}
