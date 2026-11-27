"use client";

import { useRouter } from "next/navigation";

import { Button, ButtonLink } from "@/components/ui/button";
import { ArrowIcon, SparkleIcon } from "@/components/ui/icons";
import {
  clearDemoReader,
  saveDemoReader,
  useDemoReader,
} from "@/lib/demo-session";

export function AccountPanel() {
  const router = useRouter();
  const { reader } = useDemoReader();

  const activeReader = reader ?? {
    displayName: "Maya",
    email: "maya@example.com",
    membership: "free" as const,
  };

  function handleToggleMembership() {
    saveDemoReader({
      ...activeReader,
      membership: activeReader.membership === "paid" ? "free" : "paid",
    });
  }

  function handleLogout() {
    clearDemoReader();
    router.push("/login");
  }

  return (
    <section className="mx-auto w-full max-w-2xl flex-1 px-5 py-20">
      <div className="rounded-[38px] bg-white p-8 shadow-[0_22px_70px_rgba(23,63,71,.1)] sm:p-10">
        <p className="eyebrow">My little library</p>
        <h1 className="mt-2 text-4xl font-black tracking-[-.05em] text-[#173f47] sm:text-5xl">
          Hello, {activeReader.displayName}
        </h1>
        <p className="mt-4 font-semibold text-[#61777b]">
          Your membership:{" "}
          <span className="font-black text-[#173f47]">
            {activeReader.membership === "paid" ? "Dreamer" : "Free reader"}
          </span>
        </p>

        <div className="mt-9 flex flex-wrap items-center gap-4">
          <ButtonLink href="/books" variant="primary">
            Explore stories <ArrowIcon className="size-5" />
          </ButtonLink>
          <Button
            type="button"
            variant="secondary"
            onClick={handleToggleMembership}
          >
            <SparkleIcon className="size-4 text-[#ef6d4f]" />
            {activeReader.membership === "paid"
              ? "Switch to Free preview"
              : "Preview Dreamer tier"}
          </Button>
          <Button type="button" variant="secondary" onClick={handleLogout}>
            Log out
          </Button>
        </div>
      </div>
    </section>
  );
}
