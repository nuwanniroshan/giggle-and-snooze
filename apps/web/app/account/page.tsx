import { redirect } from "next/navigation";

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

async function signOut() {
  "use server";
  const supabase = await createClient();
  await supabase.auth.signOut();
  redirect("/account");
}

export default async function Account({ searchParams }: { searchParams: Promise<{ sent?: string }> }) {
  const { sent } = await searchParams;
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();

  if (!data.user) {
    return (
      <form action={signIn} className="max-w-sm space-y-4">
        <h1 className="text-3xl font-semibold">Sign in</h1>
        <input name="email" type="email" required placeholder="you@example.com" className="w-full rounded-md border px-3 py-2" />
        <button className="rounded-md bg-primary px-5 py-2 text-sm text-primary-foreground">Email me a sign-in link</button>
        {sent && <p className="text-sm text-muted-foreground">Check your email for the link.</p>}
      </form>
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
      <form action={signOut}><button className="rounded-md border px-5 py-2 text-sm">Sign out</button></form>
    </div>
  );
}
