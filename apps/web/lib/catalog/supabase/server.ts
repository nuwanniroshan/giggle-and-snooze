import { createClient } from "@supabase/supabase-js";

// Cookie-less on purpose: catalog reads are public (published books only) and must stay cacheable.
export function createCatalogClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) {
    throw new Error("NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY are required");
  }
  return createClient(url, key, { auth: { autoRefreshToken: false, persistSession: false } });
}
