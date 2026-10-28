import { useMemo, useState } from "react";
import { Pager } from "../components/Pager";
import { QueryItem } from "../components/QueryItem";
import { Button, EmptyState, PageHeader } from "../components/ui";
import { useLive } from "../lib/live";
import { usePaged } from "../lib/usePaged";
import { useQueries } from "../lib/useQueries";

export function LiveFeed() {
  const { connected } = useLive();
  const { queries, loaded, reload } = useQueries(100);
  const [filter, setFilter] = useState("");
  const shown = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return f ? queries.filter((q) => `${q.question} ${q.sender_name} ${q.sender_id} ${(q.parts ?? [q.answer]).join(" ")}`.toLowerCase().includes(f)) : queries;
  }, [queries, filter]);

  const paged = usePaged(shown, 15, filter);

  return (
    <>
      <PageHeader title="Live Feed"
        subtitle="Every question the node receives and how it answered. Click a row to see what was retrieved and the exact prompt, which is the first place to look when an answer is wrong."
        actions={<Button icon="refresh" onClick={reload}>Refresh</Button>} />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <label className="sr-only" htmlFor="feed-filter">Filter conversations</label>
        <input id="feed-filter" className="field max-w-sm" placeholder="Filter by question, answer or sender" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <span className="text-sm text-muted" role="status">{connected ? "Listening for new messages…" : "Reconnecting to the live feed…"}</span>
      </div>
      {loaded && shown.length === 0
        ? <EmptyState title={filter ? "Nothing matches that filter" : "No messages yet"}>{filter ? null : "Messages appear here the moment they arrive. Try the test box on the Dashboard."}</EmptyState>
        : <><ul className="space-y-3" aria-live="polite">{paged.slice.map((q) => <QueryItem key={q.id} q={q} expandable />)}</ul>
          <Pager page={paged.page} pages={paged.pages} total={paged.total} size={paged.size} onPage={paged.setPage} noun="conversations" /></>}
    </>
  );
}
