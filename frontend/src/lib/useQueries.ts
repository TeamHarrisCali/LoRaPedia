import { api } from "./api";
import { useLive } from "./live";
import type { QueryRow } from "./types";
import { usePoll } from "./usePoll";

/** Recent queries: what the server stored, plus anything that arrived live since. Newest first. */
export function useQueries(limit = 50) {
  const live = useLive();
  const stored = usePoll(() => api.get<QueryRow[]>(`/api/queries?limit=${limit}`), 15000, true, [live.deliveryTick]);
  const seen = new Set(live.queries.map((q) => q.id));
  const merged = [...live.queries, ...(stored.data ?? []).filter((q) => !seen.has(q.id))].slice(0, limit);
  return { queries: merged, loaded: stored.data !== null, reload: stored.reload, error: stored.error };
}

/** The messages that were sent. Tolerates rows that only carry the joined `answer`. */
export const partsOf = (q: QueryRow): string[] => (Array.isArray(q.parts) ? q.parts : (q.answer ?? "").split("\n"));
