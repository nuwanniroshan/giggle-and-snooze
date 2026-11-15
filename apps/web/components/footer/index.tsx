import { MenuLink } from "@/components/nav/menu-link";
import { Container } from "@/components/ui/container";
import { Sections } from "@/components/ui/sections";
import { shopConfig } from "@/lib/config";

const LINKS = [
  { title: "Library", url: "/collections/all" },
  { title: "Membership", url: "/membership" },
  { title: "Terms", url: "/terms" },
  { title: "Privacy", url: "/privacy" },
  { title: "Refunds", url: "/refunds" },
];

export function Footer() {
  return (
    <footer className="bg-night text-night-foreground">
      <Container className="pt-16 pb-10">
        <Sections className="gap-10">
          <div className="flex flex-col sm:flex-row items-center justify-between gap-5">
            <div className="flex flex-wrap items-center justify-center gap-x-5 gap-y-2 sm:justify-start">
              {LINKS.map((link) => (
                <MenuLink
                  key={link.url}
                  url={link.url}
                  className="cursor-pointer rounded-full px-3 py-1 font-display text-base text-night-foreground/85 transition-colors hover:bg-night-foreground/10 hover:text-brand-yellow"
                >
                  {link.title}
                </MenuLink>
              ))}
            </div>
            <p className="text-sm text-night-foreground/70">
              © {shopConfig.site.name}. {shopConfig.site.tagline}
            </p>
          </div>
        </Sections>
      </Container>
    </footer>
  );
}
