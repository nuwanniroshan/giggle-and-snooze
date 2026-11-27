"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { saveDemoReader } from "@/lib/demo-session";

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    const formData = new FormData(event.currentTarget);
    const displayNameRaw = String(formData.get("displayName") ?? "").trim();
    const email = String(formData.get("email") ?? "").trim().toLowerCase();
    const password = String(formData.get("password") ?? "").trim();

    if (mode === "signup") {
      if (displayNameRaw.length < 2 || displayNameRaw.length > 40) {
        setError("Choose a name between 2 and 40 characters.");
        return;
      }
      if (!email.includes("@")) {
        setError("Enter a valid email address.");
        return;
      }
      if (password.length < 8) {
        setError("Use at least 8 characters for your password.");
        return;
      }
    } else {
      if (!email || !password) {
        setError("Enter your email and password.");
        return;
      }
    }

    setPending(true);
    const fallbackName = email.split("@")[0] || "Maya";
    const formattedName =
      mode === "signup"
        ? displayNameRaw
        : fallbackName.charAt(0).toUpperCase() + fallbackName.slice(1);

    saveDemoReader({
      displayName: formattedName,
      email,
      membership: "free",
    });

    router.push("/account");
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4" noValidate>
      {mode === "signup" && (
        <label className="block">
          <span className="mb-2 block text-sm font-bold text-[#173f47]">
            Reader name
          </span>
          <input
            className="auth-input"
            name="displayName"
            placeholder="Maya"
            autoComplete="name"
            required
          />
        </label>
      )}

      <label className="block">
        <span className="mb-2 block text-sm font-bold text-[#173f47]">
          Email
        </span>
        <input
          className="auth-input"
          name="email"
          type="email"
          placeholder="you@example.com"
          autoComplete="email"
          required
        />
      </label>

      <label className="block">
        <span className="mb-2 block text-sm font-bold text-[#173f47]">
          Password
        </span>
        <input
          className="auth-input"
          name="password"
          type="password"
          placeholder="At least 8 characters"
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          required
        />
      </label>

      {error && (
        <p className="rounded-2xl bg-red-50 px-4 py-3 text-sm font-semibold text-red-700">
          {error}
        </p>
      )}

      <Button
        variant="primary"
        className="w-full justify-center disabled:opacity-60"
        disabled={pending}
        type="submit"
      >
        {pending
          ? "Opening your library…"
          : mode === "login"
            ? "Log in"
            : "Create free account"}
      </Button>
    </form>
  );
}
