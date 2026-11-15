import { LegalPage } from "@/components/legal-page";
import { site } from "@/lib/site";

export const metadata = { title: "Privacy Policy" };

export default function Privacy() {
  return (
    <LegalPage title="Privacy Policy" updated="2026-11-15">
      <p>This explains what {site.name} collects and why, in plain language.</p>
      <h2>What we collect</h2>
      <ul>
        <li>Your email address and, if you use Google sign-in, the basic profile Google shares (name, email).</li>
        <li>Which books you own and your membership status.</li>
        <li>Payment details are collected and stored by Stripe. We never see your full card number.</li>
        <li>Basic technical data (such as IP address and device type) in server logs and minimal, privacy-friendly analytics.</li>
      </ul>
      <h2>How we use it</h2>
      <p>To sign you in, deliver the books you bought, run memberships, send receipts and important service emails, keep the Service safe, and improve it. We do not sell your personal information and we do not show ads.</p>
      <h2>Who we share it with</h2>
      <p>Service providers that run the Service for us: Supabase (accounts, database, file storage), Stripe (payments), Vercel (hosting), and Google if you choose Google sign-in.</p>
      <h2>Children&apos;s privacy</h2>
      <p>The Service is meant for parents and other adults. Children do not have accounts, and we do not knowingly collect personal information from children under 13 (or the age set by local law). Books are read by a child together with a grown-up on the adult&apos;s account. If you believe a child has given us personal information, contact us and we will delete it.</p>
      <p><strong>Review needed:</strong> COPPA (US) and similar children&apos;s privacy laws need a legal review before launch, including how our YouTube &quot;made for kids&quot; videos link to the store.</p>
      <h2>Your choices</h2>
      <p>You can ask to see, correct or delete your data, or close your account, by contacting us. Some records (such as payment records) must be kept for legal reasons.</p>
      <h2>Retention and security</h2>
      <p>We keep data while your account is open and as the law requires. We use industry-standard protections, but no system is perfectly secure.</p>
      <h2>Contact</h2>
      <p>[contact email to be added].</p>
    </LegalPage>
  );
}
