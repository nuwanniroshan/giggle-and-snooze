import { createClient } from "@supabase/supabase-js";

/** Service-role client. Server only: the sole writer of entitlements and memberships. */
export function createAdminClient() {
  return createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.SUPABASE_SERVICE_ROLE_KEY!, {
    auth: { persistSession: false },
  });
}
