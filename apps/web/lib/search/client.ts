"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { PredictiveSearchResult } from "@/lib/search/types";

const DEBOUNCE_MS = 250;

export function usePredictiveSearch() {
  const [activeIndex, setActiveIndex] = useState(-1);
  const [query, setTerm] = useState("");
  const [results, setResults] = useState<PredictiveSearchResult | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const controller = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    controller.current?.abort();
  }, []);

  useEffect(() => cancel, [cancel]);

  const setQuery = useCallback(
    (term: string) => {
      cancel();
      setActiveIndex(-1);
      setTerm(term);
      if (!term.trim()) {
        setResults(null);
        setIsLoading(false);
        return;
      }
      setIsLoading(true);
      timer.current = setTimeout(async () => {
        const abort = new AbortController();
        controller.current = abort;
        try {
          const response = await fetch(`/api/search/suggest?q=${encodeURIComponent(term)}`, {
            signal: abort.signal,
          });
          if (!response.ok) throw new Error("Search failed");
          setResults((await response.json()) as PredictiveSearchResult);
          setIsLoading(false);
        } catch (error) {
          if ((error as Error).name === "AbortError") return;
          setResults({ products: [], queries: [] });
          setIsLoading(false);
        }
      }, DEBOUNCE_MS);
    },
    [cancel],
  );

  const reset = useCallback(() => {
    cancel();
    setTerm("");
    setResults(null);
    setIsLoading(false);
    setActiveIndex(-1);
  }, [cancel]);

  return { activeIndex, isLoading, query, reset, results, setActiveIndex, setQuery };
}
