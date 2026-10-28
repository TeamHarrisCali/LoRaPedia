import { useMemo, useState } from "react";
import { NodePicker } from "../components/NodePicker";
import { Pager } from "../components/Pager";
import { SaveBar } from "../components/SaveBar";
import { TabPanel, Tabs, rememberTab, rememberedTab } from "../components/Tabs";
import { Badge, Button, Card, Field, PageHeader, SectionTitle } from "../components/ui";
import { api } from "../lib/api";
import { timeAgo } from "../lib/format";
import { useDraft, useSettings } from "../lib/settings";
import { useToast } from "../lib/toast";
import type { LimitNode, LimitsData } from "../lib/types";
import { usePaged } from "../lib/usePaged";
import { usePoll } from "../lib/usePoll";

const TABS = [{ id: "all", label: "Everyone" }, { id: "today", label: "Asked today" }, { id: "blocked", label: "Blocked" }, { id: "allowed", label: "Allowed" }, { id: "own", label: "Own limit" }];
const NODE_ID = /^![0-9a-f]{8}$/;

export function Limits() {
  const toast = useToast();
  const { reload } = useSettings();
  const { draft, set, dirty, save, discard } = useDraft();
  const data = usePoll(() => api.get<LimitsData>("/api/limits"), 15000);
  const [tab, setTab] = useState(() => rememberedTab("limits", "all", TABS.map((t) => t.id)));
  const [q, setQ] = useState("");
  const [extra, setExtra] = useState<string[]>([]);
  const [adding, setAdding] = useState("");

  const nodes = useMemo<LimitNode[]>(() => {
    const have = new Set(data.data?.nodes.map((n) => n.id));
    const blank = (id: string): LimitNode => ({ id, name: id, last_heard: null, asked_today: 0, asked_total: 0, last_asked: null, custom_cap: null, cap: 0, blocked: false, allowed: false });
    return [...extra.filter((id) => !have.has(id)).map(blank), ...(data.data?.nodes ?? [])].map((n) => ({
      ...n, blocked: draft ? draft.blocklist.includes(n.id) : n.blocked, allowed: draft ? draft.allowlist.includes(n.id) : n.allowed,
      custom_cap: draft ? (draft.node_daily_limits[n.id] ?? null) : n.custom_cap,
    }));
  }, [data.data, extra, draft]);

  const counts: Record<string, number> = useMemo(() => ({
    all: nodes.length, today: nodes.filter((n) => n.asked_today > 0).length, blocked: nodes.filter((n) => n.blocked).length,
    allowed: nodes.filter((n) => n.allowed).length, own: nodes.filter((n) => n.custom_cap !== null).length,
  }), [nodes]);

  const shown = useMemo(() => {
    const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const keep: Record<string, (n: LimitNode) => boolean> = { all: () => true, today: (n) => n.asked_today > 0, blocked: (n) => n.blocked, allowed: (n) => n.allowed, own: (n) => n.custom_cap !== null };
    return nodes.filter((n) => keep[tab](n) && words.every((w) => `${n.name} ${n.id}`.toLowerCase().includes(w)));
  }, [nodes, tab, q]);
  const paged = usePaged(shown, 12, `${tab}|${q}`);
  if (!draft) return <p className="text-muted" role="status">Loading…</p>;

  const defaultCap = draft.daily_limit_default;
  const flip = (list: string[], id: string, on: boolean) => (on ? [...list.filter((x) => x !== id), id] : list.filter((x) => x !== id));
  const setOwn = (id: string, raw: string) => {
    const next = { ...draft.node_daily_limits };
    if (raw.trim() === "") delete next[id]; else next[id] = Math.max(0, Math.min(1000, Math.round(Number(raw)) || 0));
    set({ node_daily_limits: next });
  };
  async function saveAll() { if (await save()) { await reload(); await data.reload(); toast.success("Limits saved."); } }
  function add() { if (NODE_ID.test(adding)) { setExtra((e) => [...new Set([adding, ...e])]); setTab("all"); setQ(adding); setAdding(""); } }

  return (
    <>
      <PageHeader title="Limits" subtitle="Who may ask, and how many questions each node may ask per day. Block someone, answer only a few chosen nodes, or give one node its own allowance." />

      <Card className="mb-5">
        <SectionTitle hint="On top of the rate limit in Settings, which only spaces questions out. A node over its limit is told once, then ignored until tomorrow (this computer's midnight). Commands such as ?ping and ?stats never count.">Daily limit</SectionTitle>
        <div className="max-w-xs">
          <Field label="Questions per node per day" htmlFor="lim-default" help="0 means no limit. Each node can have its own number below.">
            <input id="lim-default" type="number" min={0} max={1000} className="field" value={defaultCap} onChange={(e) => set({ daily_limit_default: Math.max(0, Math.min(1000, Math.round(Number(e.target.value)) || 0)) })} />
          </Field>
        </div>
        {draft.allowlist.length > 0 && <p className="text-sm text-amber-text" role="status">Only the {draft.allowlist.length} allowed node{draft.allowlist.length === 1 ? "" : "s"} below are answered; everyone else is ignored. Turn every “Allow” off to answer everyone again.</p>}
      </Card>

      <Card className="mb-5">
        <SectionTitle hint="Everyone the radio has heard or who has asked a question. Block a node to ignore it. If anything is allowed, only allowed nodes are answered.">Nodes</SectionTitle>
        <Tabs label="Which nodes" active={tab} onChange={(id) => { setTab(id); rememberTab("limits", id); }}
          tabs={TABS.map((t) => ({ ...t, badge: <Badge>{counts[t.id]}</Badge> }))} />
        <div className="mb-3 flex flex-wrap items-end gap-3">
          <input aria-label="Search nodes" type="search" className="field max-w-xs" placeholder="Search name or id…" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="flex items-end gap-2">
            <div className="w-64"><NodePicker id="lim-add" label="Add a node by id" allowId value={adding} onChange={setAdding} placeholder="Add a node: type an id…"
              nodes={nodes.map((n) => ({ id: n.id, label: n.name, detail: n.id }))} /></div>
            <Button disabled={!NODE_ID.test(adding)} onClick={add}>Add</Button>
          </div>
        </div>
        {TABS.map((t) => (
          <TabPanel key={t.id} id={t.id} active={tab}>
            {shown.length === 0 ? <p className="text-sm text-muted">{q ? "No node matches." : t.id === "all" ? "No nodes yet." : "None."}</p> : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <caption className="sr-only">Nodes, today's questions and limits</caption>
                  <thead className="text-xs uppercase tracking-wide text-muted"><tr>
                    <th className="py-2 pr-3 font-medium">Node</th><th className="pr-3 font-medium">Today</th><th className="pr-3 font-medium">Own limit</th><th className="pr-3 font-medium">Block</th><th className="font-medium">Allow</th>
                  </tr></thead>
                  <tbody>
                    {paged.slice.map((n) => {
                      const cap = n.custom_cap ?? defaultCap;
                      const full = cap > 0 && n.asked_today >= cap;
                      return (
                        <tr key={n.id} className="border-t border-line align-middle">
                          <td className="py-2 pr-3"><span className="font-medium">{n.name}</span><span className="block font-mono text-xs text-muted">{n.id}{n.last_asked ? ` · asked ${timeAgo(n.last_asked)}` : n.last_heard ? ` · heard ${timeAgo(n.last_heard)}` : ""}</span></td>
                          <td className="pr-3"><span className={full ? "font-semibold text-amber-text" : ""}>{n.asked_today}{cap > 0 ? ` of ${cap}` : ""}</span>{full && <> <Badge tone="amber">limit reached</Badge></>}<span className="block text-xs text-muted">{n.asked_total} in all</span></td>
                          <td className="pr-3"><input aria-label={`Own daily limit for ${n.name}`} type="number" min={0} max={1000} className="field w-24" placeholder={String(defaultCap)} value={n.custom_cap ?? ""} onChange={(e) => setOwn(n.id, e.target.value)} /></td>
                          <td className="pr-3"><input type="checkbox" role="switch" aria-label={`Block ${n.name}`} className="h-5 w-5" checked={n.blocked} onChange={(e) => set({ blocklist: flip(draft.blocklist, n.id, e.target.checked) })} /></td>
                          <td><input type="checkbox" role="switch" aria-label={`Allow ${n.name}`} className="h-5 w-5" checked={n.allowed} onChange={(e) => set({ allowlist: flip(draft.allowlist, n.id, e.target.checked) })} /></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </TabPanel>
        ))}
        <Pager page={paged.page} pages={paged.pages} total={paged.total} size={paged.size} onPage={paged.setPage} noun="nodes" />
        <p className="mt-3 text-sm text-muted">The own-limit box is empty when a node uses the daily limit above; 0 means no limit for that node. Changes apply when you save.</p>
      </Card>
      <SaveBar dirty={dirty} onSave={() => void saveAll()} onDiscard={discard} />
    </>
  );
}
