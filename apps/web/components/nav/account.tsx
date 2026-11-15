import { UserRoundCheckIcon, UserRoundIcon } from "lucide-react";

import { Link } from "@/components/ui/link";
import { createClient } from "@/lib/supabase/server";

async function isSignedIn(): Promise<boolean> {
  if (!process.env.NEXT_PUBLIC_SUPABASE_URL) return false;
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();
  return Boolean(data.user);
}

export async function NavAccount() {
  const signedIn = await isSignedIn();
  return (
    <Link
      href="/account"
      className="flex items-center justify-center gap-2 text-foreground hover:text-foreground/80 transition-colors"
    >
      {signedIn ? <UserRoundCheckIcon className="size-5" /> : <UserRoundIcon className="size-5" />}
      <span className="sr-only md:not-sr-only md:text-sm">{signedIn ? "Account" : "Sign in"}</span>
    </Link>
  );
}

export function NavAccountFallback() {
  return (
    <span className="flex items-center justify-center gap-2 text-foreground">
      <UserRoundIcon className="size-5" />
      <span className="sr-only md:not-sr-only md:text-sm">Account</span>
    </span>
  );
}
