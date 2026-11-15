import { NextResponse } from "next/server";

import { site } from "@/lib/site";
import { stripe } from "@/lib/stripe";
import { createAdminClient } from "@/lib/supabase/admin";
import { createClient } from "@/lib/supabase/server";

export async function POST() {
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();
  if (!data.user) return NextResponse.redirect(new URL("/account", site.url), 303);

  const admin = createAdminClient();
  const { data: profile } = await admin
    .from("profiles")
    .select("stripe_customer_id")
    .eq("id", data.user.id)
    .maybeSingle();
  if (!profile?.stripe_customer_id) return NextResponse.redirect(new URL("/account", site.url), 303);

  const session = await stripe.billingPortal.sessions.create({
    customer: profile.stripe_customer_id,
    return_url: `${site.url}/account`,
  });
  return NextResponse.redirect(session.url, 303);
}
