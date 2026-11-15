import { CheckIcon } from "lucide-react";
import { Suspense } from "react";

import { Button } from "@/components/ui/button";
import { Container } from "@/components/ui/container";
import { Page } from "@/components/ui/page";
import { Skeleton } from "@/components/ui/skeleton";
import { isMembershipActive } from "@/lib/membership";
import { createClient } from "@/lib/supabase/server";

export const metadata = {
  description: "Read every member-included Giggle & Snooze story, including new releases. Cancel any time.",
  title: "Membership",
};

const BENEFITS = [
  "Read every member-included book in full, in your browser or the reader app",
  "New releases as they are published",
  "One account for the whole family: children read on yours",
  "Cancel any time from your account",
];

async function MembershipAction() {
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();
  if (!data.user) {
    return (
      <div className="grid gap-2">
        <Button size="lg" className="h-12 w-full" render={<a href="/account" />}>
          Sign in to join
        </Button>
      </div>
    );
  }
  const { data: membership } = await supabase.from("memberships").select("status, current_period_end").maybeSingle();
  if (isMembershipActive(membership)) {
    return (
      <div className="grid gap-2">
        <p className="text-sm text-muted-foreground">You are a member. Thank you.</p>
        <Button size="lg" variant="outline" className="h-12 w-full" render={<a href="/account" />}>
          Manage
        </Button>
      </div>
    );
  }
  return (
    <form action="/api/checkout" method="post">
      <input type="hidden" name="kind" value="membership" />
      <Button type="submit" size="lg" className="h-12 w-full">
        Join
      </Button>
    </form>
  );
}

export default function Membership() {
  return (
    <Page className="pt-2.5 md:pt-10">
      <Container>
        <div className="mx-auto grid w-full max-w-2xl gap-8 py-6 md:py-10">
          <div className="grid gap-2">
            <h1 className="text-4xl">Membership</h1>
            <p className="text-muted-foreground">
              Members read every member-included book, including new releases. Cancel any time.
            </p>
          </div>
          <div className="grid gap-6 rounded-lg border p-6">
            <h2 className="text-lg">What members get</h2>
            <ul className="grid gap-3">
              {BENEFITS.map((benefit) => (
                <li key={benefit} className="flex gap-3 text-sm">
                  <CheckIcon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                  <span>{benefit}</span>
                </li>
              ))}
            </ul>
            <Suspense fallback={<Skeleton className="h-12 w-full" />}>
              <MembershipAction />
            </Suspense>
          </div>
        </div>
      </Container>
    </Page>
  );
}
