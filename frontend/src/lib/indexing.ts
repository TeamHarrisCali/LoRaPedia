import type { IndexProgress } from "./types";

/** How far indexing has got, 0 to 99 while it runs (100 is only for "done"), or null when there is nothing to show. */
export function indexPct(p: IndexProgress | null | undefined): number | null {
  if (!p || (p.state !== "running" && p.state !== "paused")) return null;
  if (!p.entries_total) return 0;
  return Math.min(99, Math.floor((p.entries_done / p.entries_total) * 100));
}
