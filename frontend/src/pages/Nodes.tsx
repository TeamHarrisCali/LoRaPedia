import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Sparkline } from "../components/Sparkline";
import { Badge, Button, Card, EmptyState, Field, PageHeader, SectionTitle } from "../components/ui";
import { api, errorText } from "../lib/api";
import { fmtDuration, timeAgo } from "../lib/format";
import { useToast } from "../lib/toast";
import type { MeshNode, MeshNodeDetail, MeshNodes, TelemetryRow } from "../lib/types";
import { usePoll } from "../lib/usePoll";

const FILTERS = [
  ["all", "All"], ["live", "In the radio's list now"], ["day", "Heard in 24 h"], ["direct", "Direct neighbours"], ["position", "With a position"],
] as const;
type Filter = (typeof FILTERS)[number][0];

const match = (n: MeshNode, f: Filter) =>
  f === "all" ? true : f === "live" ? n.live : f === "day" ? n.age_s !== null && n.age_s <= 86400 : f === "direct" ? n.hops_measured : n.lat !== null;

const num = (v: number | null | undefined, digits = 1, unit = "") => (v === null || v === undefined ? "–" : `${v.toFixed(digits)}${unit}`);
const battery = (v: number | null) => (v === null ? "–" : v > 100 ? "powered" : `${Math.round(v)}%`);
const hops = (n: MeshNode) => (n.hops === null ? "–" : n.hops === 0 ? "direct" : `${n.hops} hop${n.hops > 1 ? "s" : ""}`);

function Tile({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <p className="text-sm text-muted">{label}</p>
      <p className="mt-1 font-display text-2xl">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-muted">{sub}</p>}
    </div>
  );
}

function Series({ rows, pick, label, unit, digits = 1 }: { rows: TelemetryRow[]; pick: (r: TelemetryRow) => number | null; label: string; unit: string; digits?: number }) {
  const data = rows.map(pick).filter((v): v is number => v !== null);
  if (data.length < 2) return null;
  const last = data[data.length - 1];
  return (
    <div className="mb-3">
      <div className="flex items-baseline justify-between text-sm"><span className="text-muted">{label}</span><span>{last.toFixed(digits)}{unit} <span className="text-xs text-muted">({Math.min(...data).toFixed(digits)} to {Math.max(...data).toFixed(digits)})</span></span></div>
      <Sparkline data={data} label={`${label} over the last week`} height={44} />
    </div>
  );
}

function Detail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const toast = useToast();
  const d = usePoll(() => api.get<MeshNodeDetail>(`/api/mesh/nodes/${encodeURIComponent(id)}`), 15000);
  const n = d.data?.node;
  const [label, setLabel] = useState(""), [note, setNote] = useState(""), [busy, setBusy] = useState(false);
  useEffect(() => { setLabel(n?.label ?? ""); setNote(n?.note ?? ""); }, [n?.id, n?.label, n?.note]);

  if (d.error && !d.data) return <Card><p role="alert" className="text-danger">{errorText(d.error)}</p></Card>;
  if (!n) return <Card><p className="text-muted">Loading…</p></Card>;
  const tel = d.data?.telemetry ?? [];
  const dirty = label !== (n.label ?? "") || note !== (n.note ?? "");

  async function save() {
    setBusy(true);
    try { await api.put(`/api/mesh/nodes/${encodeURIComponent(id)}/note`, { label: label || null, note: note || null }); toast.success("Saved."); await d.reload(); onChanged(); }
    catch (e) { toast.error(errorText(e)); } finally { setBusy(false); }
  }

  const rows: [string, React.ReactNode][] = [
    ["Last heard", n.last_heard ? timeAgo(n.last_heard) : "never heard directly"],
    ["First seen", n.first_seen ? new Date(n.first_seen * 1000).toLocaleDateString() : "–"],
    ["Distance", hops(n) + (n.hops_measured ? " (measured)" : "")],
    ["Signal", n.snr === null ? "–" : `SNR ${n.snr} dB${n.rssi !== null ? `, RSSI ${n.rssi} dBm` : ""}`],
    ["Role", n.role ?? "–"], ["Hardware", n.hw ?? "–"],
    ["Battery", battery(n.battery) + (n.voltage !== null ? ` (${n.voltage.toFixed(2)} V)` : "")],
    ["Channel use", n.channel_util === null ? "–" : `${n.channel_util.toFixed(1)}% (sending ${num(n.air_util_tx, 1, "%")})`],
    ["Uptime", n.uptime_s === null ? "–" : fmtDuration(n.uptime_s)],
    ["Weather", n.temperature === null && n.humidity === null && n.pressure === null ? "–" : [num(n.temperature, 1, " °C"), num(n.humidity, 0, " %"), num(n.pressure, 0, " hPa")].join("  ")],
    ["Position", n.lat === null ? "–" : `${n.lat.toFixed(5)}, ${n.lon!.toFixed(5)}${n.alt !== null ? `, ${Math.round(n.alt)} m` : ""} (${n.pos_source ?? "?"}, ${timeAgo(n.pos_ts)})`],
  ];
  return (
    <Card as="div">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="font-display text-xl">{n.display}</h2>
        {n.us && <Badge tone="teal">this radio</Badge>}
        <Badge tone={n.live ? "teal" : "muted"}>{n.live ? "in the radio's list" : "remembered"}</Badge>
        {n.via_mqtt ? <Badge>via MQTT</Badge> : null}
      </div>
      <p className="mb-3 font-mono text-xs text-muted">{n.id}{n.short ? ` · ${n.short}` : ""}{n.name && n.name !== n.display ? ` · ${n.name}` : ""}</p>
      <dl className="mb-4 grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[8rem_1fr]">
        {rows.map(([k, v]) => <div key={k} className="contents"><dt className="text-muted">{k}</dt><dd className="break-words">{v}</dd></div>)}
      </dl>
      {tel.length > 1 && (
        <div className="mb-4">
          <SectionTitle hint="What this node reported over the last week, as heard by your radio.">History</SectionTitle>
          <Series rows={tel} pick={(r) => r.battery} label="Battery" unit="%" digits={0} />
          <Series rows={tel} pick={(r) => r.voltage} label="Voltage" unit=" V" digits={2} />
          <Series rows={tel} pick={(r) => r.channel_util} label="Channel use" unit="%" />
          <Series rows={tel} pick={(r) => r.temperature} label="Temperature" unit=" °C" />
          <Series rows={tel} pick={(r) => r.humidity} label="Humidity" unit="%" digits={0} />
        </div>
      )}
      <SectionTitle hint="Yours alone: kept on this computer, never sent over the radio.">Your label and note</SectionTitle>
      <Field label="Label" htmlFor="nl" help="Your own name for this node, shown instead of the name it gave itself."><input id="nl" className="field" maxLength={40} value={label} onChange={(e) => setLabel(e.target.value)} placeholder={n.name ?? n.short ?? ""} /></Field>
      <Field label="Note" htmlFor="nn"><textarea id="nn" className="field min-h-20" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
      <Button variant="primary" busy={busy} disabled={!dirty} onClick={() => void save()}>Save</Button>
    </Card>
  );
}

export function Nodes() {
  const toast = useToast();
  const nav = useNavigate();
  const { id } = useParams();
  const data = usePoll(() => api.get<MeshNodes>("/api/mesh/nodes"), 10000);
  const [q, setQ] = useState(""), [filter, setFilter] = useState<Filter>("all"), [busy, setBusy] = useState(false);
  const d = data.data;
  // On a narrow screen the chosen node's page sits above the list: bring it into view.
  useEffect(() => { if (id && window.innerWidth < 1280) window.scrollTo({ top: 0 }); }, [id]);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (d?.nodes ?? []).filter((n) => match(n, filter) && (!needle || [n.display, n.name, n.short, n.id, n.label, n.note, n.hw, n.role].some((v) => v?.toLowerCase().includes(needle))));
  }, [d, q, filter]);

  async function demo() {
    setBusy(true);
    try { await api.post("/api/mesh/demo"); toast.success("Added a made-up mesh."); await data.reload(); }
    catch (e) { toast.error(errorText(e)); } finally { setBusy(false); }
  }

  const s = d?.summary;
  return (
    <>
      <PageHeader title="Nodes" subtitle="Every node your radio has heard, kept on this computer. Nothing here is sent anywhere." />
      {d && !d.recording && <p role="status" className="mb-4 rounded-lg border border-line bg-surface p-3 text-sm text-amber-text">Recording is switched off in Settings, so this list is not growing.</p>}
      {data.error && !d && <p role="alert" className="mb-4 text-danger">{errorText(data.error)}</p>}
      {s && (
        <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-4">
          <Tile label="Known nodes" value={s.nodes_total} sub={`${s.heard_24h} heard in 24 h`} />
          <Tile label="Heard in the last hour" value={s.heard_1h} sub={`${s.heard_15m} in the last 15 min`} />
          <Tile label="Direct neighbours" value={s.direct} sub="heard with no relay" />
          <Tile label="With a position" value={s.with_position} sub={s.avg_channel_util !== null ? `channel use ${s.avg_channel_util}%` : undefined} />
        </div>
      )}
      {d && d.nodes.length === 0 ? (
        <Card>
          <EmptyState title="No nodes heard yet">
            {d.radio === "simulator" ? "No radio is attached, so there is nothing to hear. " : "Nodes appear here as the radio hears them. "}
            You can fill the list with a made-up mesh to see how this page works.
          </EmptyState>
          <div className="mt-3 text-center"><Button busy={busy} onClick={() => void demo()}>Add a made-up mesh</Button></div>
        </Card>
      ) : d && (
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
          <div>
            <div className="mb-3 flex flex-wrap gap-2">
              <input aria-label="Search nodes" type="search" className="field max-w-xs" placeholder="Search name, id, note…" value={q} onChange={(e) => setQ(e.target.value)} />
              <select aria-label="Show" className="field max-w-xs" value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
                {FILTERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
              <span className="self-center text-sm text-muted">{shown.length} of {d.nodes.length}</span>
            </div>
            {shown.length === 0 ? <p className="text-sm text-muted">No node matches.</p> : (
              <ul className="divide-y divide-line rounded-xl border border-line bg-surface" aria-label="Nodes">
                {shown.map((n) => (
                  <li key={n.id}>
                    <button type="button" aria-current={n.id === id ? "true" : undefined} onClick={() => nav(`/nodes/${encodeURIComponent(n.id)}`)}
                      className={`flex w-full flex-wrap items-center gap-x-3 gap-y-1 p-3 text-left hover:bg-raised ${n.id === id ? "bg-raised" : ""}`}>
                      <span className="min-w-[10rem] flex-1">
                        <span className="block truncate font-medium">{n.display}{n.us && <Badge tone="teal"> this radio</Badge>}</span>
                        <span className="block truncate font-mono text-xs text-muted">{n.short ? `${n.short} · ` : ""}{n.id}{n.hw ? ` · ${n.hw}` : ""}</span>
                      </span>
                      <span className="text-sm text-muted">{hops(n)}</span>
                      <span className="w-16 text-right text-sm text-muted">{n.snr === null ? "" : `${n.snr} dB`}</span>
                      <span className="w-16 text-right text-sm">{battery(n.battery)}</span>
                      <span className="w-20 text-right text-sm text-muted">{n.last_heard ? timeAgo(n.last_heard) : "never"}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-3 text-sm text-muted">Kept for {d.retention_days} days of history; a node you have not heard for 90 days is forgotten. Change this in Settings.</p>
          </div>
          <div className={id ? "order-first xl:order-none" : ""}>{id ? <Detail id={id} onChanged={() => void data.reload()} /> : <Card><p className="text-sm text-muted">Choose a node to see its history and add a label or note.</p>{s && Object.keys(s.hardware).length > 0 && <p className="mt-3 text-sm text-muted">Hardware around you: {Object.entries(s.hardware).map(([k, v]) => `${k} ×${v}`).join(", ")}</p>}</Card>}</div>
        </div>
      )}
    </>
  );
}
