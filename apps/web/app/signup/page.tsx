import type { Metadata } from "next";

import { AuthCard } from "@/components/auth/auth-card";
import { AuthForm } from "@/components/auth/auth-form";
import { SiteFooter } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";

export const metadata: Metadata = {
  title: "Create account",
};

export default function SignupPage() {
  return (
    <main className="flex min-h-screen flex-col bg-[#fff5d8]">
      <SiteHeader />
      <AuthCard
        eyebrow="Free to begin"
        title="Start their story shelf"
        description="Create a reader account for weekly free stories and saved favorites."
        footerPrompt="Already a member?"
        footerLinkLabel="Log in"
        footerLinkHref="/login"
      >
        <AuthForm mode="signup" />
      </AuthCard>
      <SiteFooter />
    </main>
  );
}
