import type { Metadata } from "next";

import { AccountPanel } from "@/components/auth/account-panel";
import { SiteFooter } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";

export const metadata: Metadata = {
  title: "My account",
};

export default function AccountPage() {
  return (
    <main className="flex min-h-screen flex-col bg-[#eef8f6]">
      <SiteHeader />
      <AccountPanel />
      <SiteFooter />
    </main>
  );
}
