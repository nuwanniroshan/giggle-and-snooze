import type { Metadata } from "next";

import { AuthCard } from "@/components/auth/auth-card";
import { AuthForm } from "@/components/auth/auth-form";
import { SiteFooter } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";

export const metadata: Metadata = {
  title: "Log in",
};

export default function LoginPage() {
  return (
    <main className="flex min-h-screen flex-col bg-[#eef8f6]">
      <SiteHeader />
      <AuthCard
        eyebrow="Welcome back"
        title="Ready for a story?"
        description="Log in to continue your family’s cozy reading journey."
        footerPrompt="New here?"
        footerLinkLabel="Create a free account"
        footerLinkHref="/signup"
      >
        <AuthForm mode="login" />
      </AuthCard>
      <SiteFooter />
    </main>
  );
}
