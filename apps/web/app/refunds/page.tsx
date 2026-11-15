import { LegalPage } from "@/components/legal-page";

export const metadata = { title: "Refund Policy" };

export default function Refunds() {
  return (
    <LegalPage title="Refund Policy" updated="2026-11-15">
      <p>Our books are digital goods, delivered straight away, so purchases are generally final once you have opened the book.</p>
      <h2>Books</h2>
      <p>If a book will not open, is missing pages, or you bought it by mistake and have not opened it, contact us within 14 days and we will fix it or refund you.</p>
      <h2>Memberships</h2>
      <p>You can cancel any time from the customer portal. Cancelling stops the next renewal; you keep access until the end of the period you paid for. We do not give partial refunds for unused time, except where the law requires it. If you were charged for a renewal you did not intend, contact us within 7 days.</p>
      <h2>Your legal rights</h2>
      <p>This policy does not limit rights you have under consumer law where you live.</p>
      <h2>How to ask</h2>
      <p>Email [contact email to be added] with the address on your account and the book or date of charge.</p>
    </LegalPage>
  );
}
