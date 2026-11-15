import { createClient } from "@/lib/supabase/server";

export const metadata = { title: "Membership" };

export default async function Membership() {
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();
  return (
    <div className="max-w-xl space-y-5">
      <h1 className="text-3xl font-semibold">Membership</h1>
      <p>Members read every member-included book, including new releases. Cancel any time.</p>
      {data.user ? (
        <form action="/api/checkout" method="post">
          <input type="hidden" name="kind" value="membership" />
          <button className="rounded-md bg-primary px-5 py-2 text-sm text-primary-foreground">Join</button>
        </form>
      ) : (
        <a href="/account" className="rounded-md bg-primary px-5 py-2 text-sm text-primary-foreground">Sign in to join</a>
      )}
    </div>
  );
}
