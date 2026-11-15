import { shopConfig } from "@/lib/config";
import type { Money } from "@/lib/money/types";

export function formatPrice(money: Money, locale: string = shopConfig.localization.locale): string {
  return new Intl.NumberFormat(locale, { currency: money.currencyCode, style: "currency" }).format(
    Number(money.amount),
  );
}

// Minor units follow each currency's ISO 4217 exponent, which Intl reports.
function currencyDigits(currencyCode: string): number {
  return (
    new Intl.NumberFormat("en", { currency: currencyCode, style: "currency" }).resolvedOptions()
      .maximumFractionDigits ?? 2
  );
}

export function fromMinorUnits(amount: number, currencyCode: string): Money {
  const digits = currencyDigits(currencyCode);
  return { amount: (amount / 10 ** digits).toFixed(digits), currencyCode };
}
