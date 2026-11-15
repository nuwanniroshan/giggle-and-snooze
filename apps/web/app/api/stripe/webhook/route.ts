import type Stripe from "stripe";

import { stripe } from "@/lib/stripe";
import { createAdminClient } from "@/lib/supabase/admin";

type SubWithPeriod = Stripe.Subscription & { current_period_end?: number };

async function saveMembership(sub: Stripe.Subscription) {
  const userId = sub.metadata.user_id;
  if (!userId) return;
  const end = (sub.items.data[0] as { current_period_end?: number } | undefined)?.current_period_end ?? (sub as SubWithPeriod).current_period_end;
  await createAdminClient().from("memberships").upsert({
    user_id: userId,
    stripe_subscription_id: sub.id,
    status: sub.status,
    current_period_end: end ? new Date(end * 1000).toISOString() : null,
    updated_at: new Date().toISOString(),
  });
}

export async function POST(request: Request) {
  const body = await request.text();
  let event: Stripe.Event;
  try {
    event = stripe.webhooks.constructEvent(body, request.headers.get("stripe-signature") ?? "", process.env.STRIPE_WEBHOOK_SECRET!);
  } catch {
    return new Response("bad signature", { status: 400 });
  }
  const admin = createAdminClient();

  if (event.type === "checkout.session.completed") {
    const session = event.data.object;
    const userId = session.client_reference_id;
    if (userId && typeof session.customer === "string") {
      await admin.from("profiles").update({ stripe_customer_id: session.customer }).eq("id", userId);
    }
    if (session.mode === "payment" && session.payment_status === "paid" && session.metadata?.book_id && userId) {
      await admin.from("entitlements").upsert({
        user_id: userId,
        book_id: session.metadata.book_id,
        source: "purchase",
        stripe_payment_intent: typeof session.payment_intent === "string" ? session.payment_intent : null,
      });
    }
    if (session.mode === "subscription" && typeof session.subscription === "string") {
      await saveMembership(await stripe.subscriptions.retrieve(session.subscription));
    }
  } else if (event.type === "customer.subscription.updated" || event.type === "customer.subscription.deleted") {
    await saveMembership(event.data.object);
  }
  return new Response("ok");
}
