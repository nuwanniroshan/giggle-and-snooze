import { LegalPage } from "@/components/legal-page";
import { site } from "@/lib/site";

export const metadata = { title: "Terms of Service" };

export default function Terms() {
  return (
    <LegalPage title="Terms of Service" updated="2026-11-15">
      <p>These terms cover your use of {site.name} (the &quot;Service&quot;). By creating an account or buying a book you agree to them.</p>
      <h2>Who can use the Service</h2>
      <p>The Service is for parents, guardians and other adults. You must be 18 or older to create an account or buy anything. Children read with a grown-up; they do not get their own accounts.</p>
      <h2>Your account</h2>
      <p>Keep your sign-in details safe. You are responsible for what happens under your account.</p>
      <h2>What you get</h2>
      <p>Buying a book gives you a personal, non-transferable licence to read it in our reader for your own family. A membership gives you access to member-included books while it is active. Books may be added or removed from membership over time.</p>
      <h2>What you may not do</h2>
      <p>Do not copy, resell, share account access, or remove protections from our books, pictures or audio. All stories and artwork belong to {site.name} or its creators.</p>
      <h2>Memberships and payments</h2>
      <p>Payments are handled by Stripe. Memberships renew until you cancel, and you can cancel any time from the customer portal; access continues to the end of the paid period. See the Refund Policy for refunds.</p>
      <h2>Changes and availability</h2>
      <p>We may update the Service or these terms. We will try to give notice of important changes. The Service is provided &quot;as is&quot; to the extent the law allows.</p>
      <h2>Contact</h2>
      <p>Questions: [contact email to be added].</p>
    </LegalPage>
  );
}
