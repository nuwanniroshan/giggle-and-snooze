import { redirect } from "next/navigation";

import { supabaseConfigured } from "@/lib/books";
import { SetupNote } from "@/components/setup-note";
import { Button } from "@/components/ui/button";
import { createClient } from "@/lib/supabase/server";
import { site } from "@/lib/site";

export const metadata = { title: "Account" };

async function signIn(formData: FormData) {
  "use server";
  const supabase = await createClient();
  await supabase.auth.signInWithOtp({
    email: String(formData.get("email")),
    options: { emailRedirectTo: `${site.url}/auth/callback` },
  });
  redirect("/account?sent=1");
}

async function signInWithGoogle() {
  "use server";
  const supabase = await createClient();
  const { data } = await supabase.auth.signInWithOAuth({
    provider: "google",
    options: { redirectTo: `${site.url}/auth/callback` },
  });
  redirect(data.url ?? "/account?error=google");
}

async function signOut() {
  "use server";
  const supabase = await createClient();
  await supabase.auth.signOut();
  redirect("/account");
}

export default async function Account({ searchParams }: { searchParams: Promise<{ sent?: string; error?: string }> }) {
  const { sent, error } = await searchParams;
  if (!supabaseConfigured()) return <SetupNote />;
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();

  if (!data.user) {
    return (
      <div className="max-w-sm space-y-6">
        <h1 className="text-3xl font-semibold">Sign in</h1>
        <p className="text-sm text-muted-foreground">For parents and grown-ups. Children read on your account.</p>
        <form action={signInWithGoogle}>
          <Button type="submit" variant="outline" size="lg" className="w-full">Continue with Google</Button>
        </form>
        <p className="text-center text-xs text-muted-foreground">or</p>
        <form action={signIn} className="space-y-4">
          <input name="email" type="email" required placeholder="you@example.com" className="w-full rounded-md border bg-background px-3 py-2" />
          <Button type="submit" size="lg" className="w-full">Email me a sign-in link</Button>
        </form>
        {sent && <p className="text-sm text-muted-foreground">Check your email for the link.</p>}
        {error && <p className="text-sm text-destructive">Google sign-in is not available right now. Try the email link.</p>}
      </div>
    );
  }

  const { data: membership } = await supabase.from("memberships").select("status, current_period_end").maybeSingle();
  const { data: owned } = await supabase.from("entitlements").select("books(slug, title)");
  return (
    <div className="max-w-xl space-y-6">
      <h1 className="text-3xl font-semibold">Your account</h1>
      <p className="text-sm text-muted-foreground">{data.user.email}</p>
      <p>Membership: {membership ? membership.status : "none"}</p>
      <section>
        <h2 className="font-medium">Your books</h2>
        <ul className="list-disc pl-5 text-sm">
          {(owned ?? []).map((row, i) => {
            const b = (row as unknown as { books: { slug: string; title: string } | null }).books;
            return b ? <li key={i}><a href={`/books/${b.slug}`}>{b.title}</a></li> : null;
          })}
        </ul>
      </section>
      <form action={signOut}><Button type="submit" variant="outline">Sign out</Button></form>
    </div>
  );
}
