export interface MembershipRow {
  current_period_end: string | null;
  status: string;
}

/** Mirrors the active test inside the database function `can_read_book`. */
export function isMembershipActive(row: MembershipRow | null | undefined): boolean {
  if (!row) return false;
  if (row.status !== "active" && row.status !== "trialing") return false;
  return !row.current_period_end || new Date(row.current_period_end).getTime() > Date.now();
}

export function formatDate(value: string | null): string | null {
  if (!value) return null;
  return new Intl.DateTimeFormat("en-US", { dateStyle: "long", timeZone: "UTC" }).format(new Date(value));
}
