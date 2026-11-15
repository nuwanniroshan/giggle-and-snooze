import { Suspense } from "react";

import { Container } from "@/components/ui/container";
import { Link } from "@/components/ui/link";
import { shopConfig } from "@/lib/config";
import type { MenuItem } from "@/lib/menu/types";

import { NavAccount, NavAccountFallback } from "./account";
import { MobileMenu } from "./mobile-menu";
import { QuickLinks } from "./quick-links";
import { SearchModal } from "./search-modal";

const NAV_ITEMS: MenuItem[] = [
  { id: "nav-library", items: [], title: "Library", url: "/collections/all" },
  { id: "nav-collections", items: [], title: "Collections", url: "/collections" },
  { id: "nav-membership", items: [], title: "Membership", url: "/membership" },
];

// The desktop bar shows the account link on the right; the mobile menu lists it as a plain item.
const MOBILE_ITEMS: MenuItem[] = [
  ...NAV_ITEMS,
  { id: "nav-account", items: [], title: "Account", url: "/account" },
];

export function Nav() {
  return (
    <nav
      className="sticky top-0 z-30 w-full bg-background pt-[env(safe-area-inset-top,0px)] transition-shadow duration-250"
      id="nav-outer"
    >
      <Container className="flex h-16 items-center gap-2.5 md:gap-5">
        <MobileMenu items={MOBILE_ITEMS} />

        <Link className="flex items-center shrink-0" href="/">
          <span className="text-xl leading-4">{shopConfig.site.name}</span>
        </Link>

        <QuickLinks items={NAV_ITEMS} />

        <div className="flex items-center gap-5 ml-auto">
          {shopConfig.search.isEnabled && <SearchModal />}
          <Suspense fallback={<NavAccountFallback />}>
            <NavAccount />
          </Suspense>
        </div>
      </Container>
    </nav>
  );
}
