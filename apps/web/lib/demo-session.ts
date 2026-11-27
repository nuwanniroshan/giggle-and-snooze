"use client";

import { useEffect, useState } from "react";

export type DemoReader = {
  displayName: string;
  email: string;
  membership: "free" | "paid";
};

const STORAGE_KEY = "gns_demo_reader";
const SESSION_EVENT = "gns-demo-session-change";

export function readDemoReader(): DemoReader | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as DemoReader;
  } catch {
    return null;
  }
}

export function saveDemoReader(reader: DemoReader): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(reader));
  window.dispatchEvent(new Event(SESSION_EVENT));
}

export function clearDemoReader(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(STORAGE_KEY);
  window.dispatchEvent(new Event(SESSION_EVENT));
}

export function useDemoReader() {
  const [reader, setReader] = useState<DemoReader | null>(null);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    const sync = () => setReader(readDemoReader());
    sync();
    setHydrated(true);

    window.addEventListener("storage", sync);
    window.addEventListener(SESSION_EVENT, sync);
    return () => {
      window.removeEventListener("storage", sync);
      window.removeEventListener(SESSION_EVENT, sync);
    };
  }, []);

  return { reader, hydrated };
}
