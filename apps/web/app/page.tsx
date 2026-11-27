import { FeaturedBooksSection } from "@/components/home/featured-books-section";
import { HeroSection } from "@/components/home/hero-section";
import { HowItWorksSection } from "@/components/home/how-it-works-section";
import { MembershipSection } from "@/components/home/membership-section";
import { TrustStrip } from "@/components/home/trust-strip";
import { SiteFooter } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";
import { getPublishedBooks } from "@/lib/books";

export default async function HomePage() {
  const books = await getPublishedBooks();
  const featured = books[0];

  return (
    <main className="overflow-hidden bg-[#fffdf8]">
      <SiteHeader />
      <HeroSection featured={featured} />
      <TrustStrip />
      <FeaturedBooksSection books={books} />
      <HowItWorksSection />
      <MembershipSection />
      <SiteFooter />
    </main>
  );
}
