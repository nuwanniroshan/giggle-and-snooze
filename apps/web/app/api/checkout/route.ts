import { NextResponse } from "next/server";

import { site } from "@/lib/site";
import { stripe } from "@/lib/stripe";
import { createAdminClient } from "@/lib/supabase/admin";
import { createClient } from "@/lib/supabase/server";

export async function POST(request: Request) {
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();
  if (!data.user) return NextResponse.redirect(new URL("/account", site.url), 303);

  const form = await request.formData();
  const kind = String(form.get("kind"));
  const admin = createAdminClient();
  const { data: profile } = await admin.from("profiles").select("stripe_customer_id").eq("id", data.user.id).single();
  const customer = profile?.stripe_customer_id ?? undefined;
  const base = { client_reference_id: data.user.id, ...(customer ? { customer } : { customer_email: data.user.email }) };

  if (kind === "membership") {
    const session = await stripe.checkout.sessions.create({
      ...base,
      mode: "subscription",
      line_items: [{ price: process.env.STRIPE_MEMBERSHIP_PRICE_ID!, quantity: 1 }],
      subscription_data: { metadata: { user_id: data.user.id } },
      success_url: `${site.url}/account`,
      cancel_url: `${site.url}/membership`,
    });
    return NextResponse.redirect(session.url!, 303);
  }

  const slug = String(form.get("slug"));
  const { data: book } = await supabase.from("books").select("id, slug, title, price_cents, currency, stripe_price_id").eq("slug", slug).eq("access", "paid").eq("published", true).single();
  if (!book) return NextResponse.redirect(new URL("/collections/all", site.url), 303);
  const session = await stripe.checkout.sessions.create({
    ...base,
    mode: "payment",
    line_items: [
      book.stripe_price_id
        ? { price: book.stripe_price_id, quantity: 1 }
        : { price_data: { currency: book.currency, unit_amount: book.price_cents!, product_data: { name: book.title } }, quantity: 1 },
    ],
    metadata: { user_id: data.user.id, book_id: book.id },
    success_url: `${site.url}/products/${book.slug}`,
    cancel_url: `${site.url}/products/${book.slug}`,
  });
  return NextResponse.redirect(session.url!, 303);
}
