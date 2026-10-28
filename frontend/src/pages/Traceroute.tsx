import { NodePicker } from "../components/NodePicker";
import { Pager } from "../components/Pager";
import { usePaged } from "../lib/usePaged";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { MeshMap, type MapNode, type MapRoute } from "../components/MeshMap";
import { Badge, Button, Card, EmptyState, Field, PageHeader, SectionTitle } from "../components/ui";
import { api, errorText } from "../lib/api";
import { timeAgo } from "../lib/format";
import { useSettings } from "../lib/settings";
import { useToast } from "../lib/toast";
import type { MeshNodes, TraceHop, TraceRow, TraceState } from "../lib/types";
import { fmtDistance, useUnits } from "../lib/units";
import { usePoll } from "../lib/usePoll";

const hopName = (h: TraceHop) => h.name ?? h.id ?? "Unknown node";

function Path({ title, hops }: { title: string; hops: TraceHop[] | null }) {
  if (!hops) return <div><h3 className="mb-1 text-sm font-medium">{title}</h3><p className="text-sm text-muted">The reply did not include this direction.</p></div>;
  return (
    <div>
      <h3 className="mb-1 text-sm font-medium">{title}</h3>
      <ol className="space-y-1 text-sm">
        {hops.map((h, i) => (
          <li key={i} className="flex flex-wrap items-baseline gap-x-2">
            <span className="w-5 text-right text-muted">{i}</span>
            {h.id ? <Link className="underline" to={`/nodes/${encodeURIComponent(h.id)}`}>{hopName(h)}</Link> : <span>{hopName(h)}</span>}
            <span className="font-mono text-xs text-muted">{h.id ?? ""}</span>
            <span className="ml-auto text-muted">{i === 0 ? "start" : h.snr === null ? "signal unknown" : `${h.snr} dB`}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function TraceView({ t }: { t: TraceRow }) {
  const units = useUnits();
  const known = (hops: TraceHop[] | null) => (hops ?? []).map((h) => (h.lat !== null ? { lat: h.lat, lon: h.lon!, label: hopName(h) } : null));
  const routes: MapRoute[] = [{ id: "there", hops: known(t.path_towards) }, ...(t.path_back ? [{ id: "back", hops: known(t.path_back), back: true }] : [])];
  const placed = [...(t.path_towards ?? []), ...(t.path_back ?? [])].filter((h) => h.lat !== null);
  const seen = new Set<string>();
  const nodes: MapNode[] = placed.filter((h) => h.id && !seen.has(h.id) && seen.add(h.id)).map((h, i) => ({
    id: h.id!, label: hopName(h), short: hopName(h).slice(0, 16), lat: h.lat!, lon: h.lon!, tone: "fresh", us: i === 0, direct: false,
  }));
  if (t.status !== "ok") return <p className={`text-sm ${t.status === "error" ? "text-danger" : "text-amber-text"}`} role="status">{t.detail}</p>;
  return (
    <div>
      <p className="mb-3 text-sm text-muted">
        {t.relays_towards} relay{t.relays_towards === 1 ? "" : "s"} there{t.relays_back !== null ? `, ${t.relays_back} back` : ""} · answered in {t.latency_ms != null ? (t.latency_ms / 1000).toFixed(1) : "?"} s · hop limit {t.hop_limit}
        {t.detail ? ` · ${t.detail}` : ""}
      </p>
      <div className="grid gap-6 md:grid-cols-2"><Path title="There" hops={t.path_towards} /><Path title="And back" hops={t.path_back} /></div>
      <div className="mt-4">
        {nodes.length >= 2 ? (
          <>
            <MeshMap nodes={nodes} trails={[]} routes={routes} selected={null} onSelect={() => {}} formatDistance={(km) => fmtDistance(km, units.distance)} />
            <p className="mt-2 text-sm text-muted">Solid amber is the way there, dashed teal the way back, and a dotted line crosses a stretch where a node's position is not known.</p>
          </>
        ) : <p className="text-sm text-muted">Too few nodes on this route have a known position to draw it.</p>}
      </div>
    </div>
  );
}

export function Traceroute() {
  const toast = useToast();
  const { settings } = useSettings();
  const data = usePoll(() => api.get<MeshNodes>("/api/mesh/nodes"), 15000);
  const history = usePoll(() => api.get<{ traceroutes: TraceRow[] }>("/api/mesh/traceroutes?limit=30"), 20000);
  const [target, setTarget] = useState("");
  const [hops, setHops] = useState(3);
  const [confirm, setConfirm] = useState(false);
  const [running, setRunning] = useState<{ id: number; simulated: boolean; since: number } | null>(null);
  const [state, setState] = useState<TraceState | null>(null);
  const [shown, setShown] = useState<TraceRow | null>(null);
  const [now, setNow] = useState(Date.now());
  useEffect(() => { if (settings && hops === 3) setHops(settings.hop_limit); }, [settings]); // eslint-disable-line react-hooks/exhaustive-deps

  const nodes = useMemo(() => (data.data?.nodes ?? []).filter((n) => !n.us).sort((a, b) => a.display.localeCompare(b.display)), [data.data]);
  const simulated = data.data?.radio !== "meshtastic";

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(async () => {
      setNow(Date.now());
      try {
        const st = await api.get<TraceState>(`/api/mesh/traceroute/${running.id}`);
        setState(st);
        if (st.status !== "waiting") { setRunning(null); if (st.trace) setShown(st.trace); void history.reload(); }
      } catch (e) { setRunning(null); toast.error(errorText(e)); }
    }, 1000);
    return () => clearInterval(timer);
  }, [running]); // eslint-disable-line react-hooks/exhaustive-deps

  async function send() {
    setConfirm(false); setState(null); setShown(null);
    try {
      const r = await api.post<{ id: number; simulated: boolean }>("/api/mesh/traceroute", { node_id: target, hop_limit: hops });
      setRunning({ id: r.id, simulated: r.simulated, since: Date.now() });
    } catch (e) { toast.error(errorText(e)); }
  }

  const name = nodes.find((n) => n.id === target)?.display ?? target;
  const historyPage = usePaged(history.data?.traceroutes ?? [], 8);
  return (
    <>
      <PageHeader title="Traceroute" subtitle="Ask the mesh which way a packet takes to a node and back, and with what signal at each hop." />
      <Card className="mb-5">
        <SectionTitle hint="This sends a packet to one node, and every node on the path repeats it. It is only ever sent when you press the button.">Trace a route</SectionTitle>
        {nodes.length === 0 ? <EmptyState title="No nodes yet">A traceroute needs a node the radio has heard. The Nodes page can load a made-up mesh to try this.</EmptyState> : (
          <>
            <div className="grid gap-x-4 sm:grid-cols-[1fr_12rem]">
              <Field label="To" htmlFor="tr-node" help={`${nodes.length} nodes known. Type part of a name or id to find one.`}><NodePicker id="tr-node" label="Node to trace" value={target} onChange={(id) => { setTarget(id); setConfirm(false); }}
                nodes={nodes.map((n) => ({ id: n.id, label: n.display, detail: `${n.id} · ${n.hops === null ? "?" : n.hops === 0 ? "direct" : `${n.hops} hop${n.hops > 1 ? "s" : ""}`}` }))} /></Field>
              <Field label="Hop limit" htmlFor="tr-hops" help="How many relays it may pass through. More reaches farther but loads the mesh more."><select id="tr-hops" className="field" value={hops} onChange={(e) => setHops(Number(e.target.value))}>{[1, 2, 3, 4, 5, 6, 7].map((h) => <option key={h} value={h}>{h}</option>)}</select></Field>
            </div>
            {simulated && <p role="status" className="mb-3 rounded-lg border border-line bg-bg p-3 text-sm">No radio is connected, so this makes up a route from the nodes it knows. <b>Nothing is transmitted.</b></p>}
            {confirm ? (
              <div className="rounded-lg border border-line bg-bg p-3 text-sm" role="alertdialog" aria-label="Confirm traceroute">
                <p>{simulated ? `Make up a route to ${name}?` : `Send a traceroute to ${name} now? It uses shared airtime: every node on the path repeats the packet. Only one can run at a time, and the same node cannot be traced again for 30 seconds.`}</p>
                <div className="mt-2 flex gap-2"><Button variant="primary" onClick={() => void send()}>{simulated ? "Make it up" : "Send traceroute"}</Button><Button variant="ghost" onClick={() => setConfirm(false)}>Cancel</Button></div>
              </div>
            ) : <Button variant="primary" icon="send" disabled={!target || !!running} busy={!!running} onClick={() => setConfirm(true)}>{running ? `Waiting for ${name}…` : "Trace the route…"}</Button>}
            {running && <p className="mt-2 text-sm text-muted" role="status">Waiting for the answer: {Math.round((now - running.since) / 1000)} s of up to 60.</p>}
          </>
        )}
      </Card>

      {(shown || (state && state.status !== "waiting")) && (
        <Card className="mb-5" as="div">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h2 className="font-display text-lg">{shown?.node_name ?? name}</h2>
            <Badge tone={(shown?.status ?? state?.status) === "ok" ? "teal" : "amber"}>{shown?.status ?? state?.status}</Badge>
            {shown && <span className="text-sm text-muted">{timeAgo(shown.ts)}</span>}
          </div>
          {shown ? <TraceView t={shown} /> : <p className="text-sm" role="status">{state?.detail}</p>}
        </Card>
      )}

      <Card>
        <SectionTitle hint="Every traceroute is saved, with where each node was at the time, so a route can be looked at again.">History</SectionTitle>
        {(history.data?.traceroutes.length ?? 0) === 0 ? <p className="text-sm text-muted">No traceroute yet.</p> : (
          <ul className="divide-y divide-line" aria-label="Traceroute history">
            {historyPage.slice.map((t) => (
              <li key={t.id}>
                <button type="button" className={`flex w-full flex-wrap items-center gap-x-3 gap-y-1 py-2 text-left text-sm hover:bg-raised ${shown?.id === t.id ? "bg-raised" : ""}`} onClick={() => { setShown(t); window.scrollTo({ top: 0 }); }}>
                  <Badge tone={t.status === "ok" ? "teal" : "amber"}>{t.status}</Badge>
                  <span className="font-medium">{t.node_name ?? t.node_id}</span>
                  <span className="text-muted">{t.status === "ok" ? `${t.relays_towards} relay${t.relays_towards === 1 ? "" : "s"} there` : t.detail}</span>
                  <span className="ml-auto text-muted">{timeAgo(t.ts)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <Pager page={historyPage.page} pages={historyPage.pages} total={historyPage.total} size={historyPage.size} onPage={historyPage.setPage} noun="traceroutes" />
      </Card>
    </>
  );
}
