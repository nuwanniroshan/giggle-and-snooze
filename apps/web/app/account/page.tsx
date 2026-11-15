import Image from "next/image";
import { redirect } from "next/navigation";
import { Suspense } from "react";

import { Container } from "@/components/ui/container";
import { Page } from "@/components/ui/page";
import { Button } from "@/components/ui/button";
import { Link } from "@/components/ui/link";
import { toCoverUrl } from "@/lib/catalog/transforms";
import { formatDate, isMembershipActive } from "@/lib/membership";
import { createAdminClient } from "@/lib/supabase/admin";
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

const FIELD =
  "h-10 w-full rounded-lg border bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50";

type OwnedBook = { cover_path: string | null; slug: string; title: string };

async function AccountContent({ searchParams }: { searchParams: Promise<{ sent?: string; error?: string }> }) {
  const { sent, error } = await searchParams;
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();

  if (!data.user) {
    return (
      <div className="mx-auto grid w-full max-w-sm gap-6 py-6 md:py-10">
        <div className="grid gap-1">
          <h1 className="text-3xl">Sign in</h1>
          <p className="text-sm text-muted-foreground">
            For parents and grown-ups. Children read on your account.
          </p>
        </div>
        <form action={signInWithGoogle}>
          <Button type="submit" variant="outline" size="lg" className="h-12 w-full">
            Continue with Google
          </Button>
        </form>
        <p className="text-center text-xs text-muted-foreground">or</p>
        <form action={signIn} className="grid gap-3">
          <input name="email" type="email" required autoComplete="email" placeholder="you@example.com" aria-label="Email" className={FIELD} />
          <Button type="submit" size="lg" className="h-12 w-full">
            Email me a sign-in link
          </Button>
        </form>
        {sent ? <p className="text-sm text-muted-foreground">Check your email for the link.</p> : null}
        {error ? (
          <p className="text-sm text-destructive">
            Google sign-in is not available right now. Try the email link.
          </p>
        ) : null}
      </div>
    );
  }

  const [{ data: membership }, { data: owned }, { data: profile }] = await Promise.all([
    supabase.from("memberships").select("status, current_period_end").maybeSingle(),
    supabase.from("entitlements").select("books(slug, title, cover_path)"),
    process.env.SUPABASE_SERVICE_ROLE_KEY
      ? createAdminClient().from("profiles").select("stripe_customer_id").eq("id", data.user.id).maybeSingle()
      : Promise.resolve({ data: null }),
  ]);
  const active = isMembershipActive(membership);
  const renews = formatDate(membership?.current_period_end ?? null);
  const books = (owned ?? [])
    .map((row) => (row as unknown as { books: OwnedBook | null }).books)
    .filter((b): b is OwnedBook => b !== null);

  return (
    <div className="mx-auto grid w-full max-w-3xl gap-10 py-6 md:py-10">
      <div className="grid gap-1">
        <h1 className="text-3xl">Your account</h1>
        <p className="text-sm text-muted-foreground">{data.user.email}</p>
      </div>

      <section className="grid gap-3 rounded-lg border p-5">
        <h2 className="text-lg">Membership</h2>
        {membership ? (
          <p className="text-sm">
            <span className="capitalize">{membership.status.replace("_", " ")}</span>
            {renews ? (
              <span className="text-muted-foreground">
                {active ? ` · renews or ends ${renews}` : ` · period ended ${renews}`}
              </span>
            ) : null}
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">No membership yet.</p>
        )}
        <div className="flex flex-wrap gap-2.5">
          {profile?.stripe_customer_id ? (
            <form action="/api/billing-portal" method="post">
              <Button type="submit" variant="outline">Manage membership</Button>
            </form>
          ) : null}
          {!active ? <Button render={<a href="/membership" />}>See membership</Button> : null}
        </div>
      </section>

      <section className="grid gap-4">
        <h2 className="text-lg">Your books</h2>
        {books.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Books you buy appear here. <Link href="/collections/all" className="underline">Browse the library</Link>
          </p>
        ) : (
          <ul className="grid grid-cols-2 gap-5 sm:grid-cols-3">
            {books.map((b) => {
              const cover = toCoverUrl(b.cover_path);
              return (
                <li key={b.slug}>
                  <Link href={`/products/${b.slug}`} className="grid gap-2 hover:opacity-80 transition-opacity">
                    <span className="relative aspect-square overflow-hidden bg-accent">
                      {cover ? (
                        <Image src={cover} alt={`Cover of ${b.title}`} fill sizes="320px" className="object-cover" />
                      ) : null}
                    </span>
                    <span className="text-sm">{b.title}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <form action={signOut}>
        <Button type="submit" variant="outline">Sign out</Button>
      </form>
    </div>
  );
}

export default function Account(props: { searchParams: Promise<{ sent?: string; error?: string }> }) {
  return (
    <Page className="pt-2.5 md:pt-10">
      <Container>
        <Suspense fallback={null}>
          <AccountContent {...props} />
        </Suspense>
      </Container>
    </Page>
  );
}
