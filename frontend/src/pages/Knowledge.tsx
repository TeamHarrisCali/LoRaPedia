import { useState } from "react";
import { IndexControls } from "../components/IndexControls";
import { TabPanel, Tabs, rememberTab, rememberedTab } from "../components/Tabs";
import { WikipediaPackages } from "../components/WikipediaPackages";
import { ZimLibrary } from "../components/ZimLibrary";
import { Badge, Button, Card, EmptyState, PageHeader, SectionTitle } from "../components/ui";
import { api, errorText } from "../lib/api";
import { fmtBytes, timeAgo } from "../lib/format";
import { indexPct } from "../lib/indexing";
import { useToast } from "../lib/toast";
import type { IndexProgress, KbStats, SearchResult } from "../lib/types";
import { usePoll } from "../lib/usePoll";

const TABS = [{ id: "library", label: "Library" }, { id: "indexing", label: "Indexing" }, { id: "search", label: "Test a search" }];

/** What the Indexing tab shows when you are looking at another tab: whether work is going on, and how far it has got. */
function indexBadge(p: IndexProgress | null | undefined) {
  if (!p) return undefined;
  if (p.state === "running") return <Badge tone="amber">{p.entries_total ? `${indexPct(p)}%` : "running"}</Badge>;
  if (p.state === "paused") return <Badge tone="muted">paused</Badge>;
  if (p.state === "error") return <Badge tone="danger">stopped</Badge>;
  return undefined;
}

export function Knowledge() {
  const toast = useToast();
  const stats = usePoll(() => api.get<KbStats>("/api/kb/stats"), 5000);
  const indexing = usePoll(() => api.get<IndexProgress>("/api/kb/index/status"), 3000);
  const [tab, setTab] = useState(() => rememberedTab("knowledge", "library", TABS.map((t) => t.id)));
  const [q, setQ] = useState("");
  const [res, setRes] = useState<{ mode: string; results: SearchResult[] } | null>(null);
  const [busy, setBusy] = useState(false);

  async function search() {
    if (!q.trim()) return;
    setBusy(true);
    try { setRes(await api.get(`/api/kb/search?q=${encodeURIComponent(q)}&k=8`)); } catch (e) { toast.error(errorText(e)); } finally { setBusy(false); }
  }
  const s = stats.data;
  const tile = (label: string, value: string) => <div className="rounded-xl border border-line bg-surface/90 p-4"><p className="text-xs font-medium uppercase tracking-wide text-muted">{label}</p><p className="mt-1 font-serif text-2xl font-semibold">{value}</p></div>;

  return (
    <>
      <PageHeader title="Knowledge" subtitle="The offline Wikipedia the node answers from." />
      <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {tile("Articles", s ? s.articles.toLocaleString() : "–")}
        {tile("Chunks", s ? s.chunks.toLocaleString() : "–")}
        {tile("Disk used", s ? fmtBytes(s.disk_bytes) : "–")}
        {tile("Last indexed", s ? timeAgo(s.last_indexed) : "–")}
      </div>
      <Tabs label="Knowledge sections" tabs={TABS.map((t) => (t.id === "indexing" ? { ...t, badge: indexBadge(indexing.data) } : t))} active={tab} onChange={(id) => { setTab(id); rememberTab("knowledge", id); }} />

      <TabPanel id="library" active={tab}>
      <div className="mb-5"><WikipediaPackages onChanged={() => void stats.reload()} /></div>
      <details className="mb-5 rounded-xl border border-line bg-surface/90 p-1">
        <summary className="cursor-pointer px-4 py-3 font-serif text-lg font-semibold">Other files and advanced options</summary>
        <div className="p-1"><ZimLibrary onChanged={() => void stats.reload()} /></div>
      </details>
      </TabPanel>

      <TabPanel id="indexing" active={tab}>
      <div className="mb-5"><IndexControls onDone={() => void stats.reload()} /></div>
      </TabPanel>

      <TabPanel id="search" active={tab}>
      <Card>
        <SectionTitle hint="Type a question the way a person would. You will see exactly what the node would pull from Wikipedia, ranked.">Search playground</SectionTitle>
        <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); void search(); }}>
          <label className="sr-only" htmlFor="pg-q">Search query</label>
          <input id="pg-q" className="field min-w-56 flex-1" placeholder="e.g. how long to boil water" value={q} onChange={(e) => setQ(e.target.value)} />
          <Button type="submit" variant="primary" icon="search" busy={busy}>Search</Button>
        </form>
        {res && (
          <div className="mt-4">
            {res.mode === "demo" && <p className="mb-3 text-sm text-amber-text" role="status">Searching the small built-in demo set. Index a Wikipedia file to search the real thing.</p>}
            {res.results.length === 0 ? <EmptyState title="Nothing relevant found">The node would answer "not found" to this.</EmptyState> : (
              <ol className="space-y-2">
                {res.results.map((r, i) => (
                  <li key={i} className="rounded-lg border border-line bg-bg p-3">
                    <div className="mb-1 flex flex-wrap items-center gap-2"><span className="font-mono text-xs text-muted">#{i + 1}</span><span className="font-semibold">{r.title}</span>{r.section && <Badge>{r.section}</Badge>}
                      <span className="ml-auto font-mono text-xs text-muted">score {r.score}</span></div>
                    <p className="text-sm">{r.text}</p>
                    {Object.keys(r.detail).length > 0 && <p className="mt-1 font-mono text-xs text-muted">keyword rank {r.detail.fts_rank ?? "–"} · vector rank {r.detail.vec_rank ?? "–"} · title match {r.detail.title_hit} · coverage {r.detail.coverage}</p>}
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}
      </Card>
      </TabPanel>
    </>
  );
}
