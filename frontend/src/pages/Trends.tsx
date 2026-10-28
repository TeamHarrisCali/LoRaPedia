import { useMemo, useState } from "react";
import { Card, EmptyState, Field, PageHeader, SectionTitle } from "../components/ui";
import { api, errorText } from "../lib/api";
import type { MeshHopTrend, MeshPacketTrend } from "../lib/types";
import { usePoll } from "../lib/usePoll";

const HOUR_MS = 3600_000;
const pad = (n: number) => String(n).padStart(2, "0");
const localDate = (hour: number) => { const d = new Date(hour * HOUR_MS); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
const kindName = (k: string) => k.replace(/_APP$/, "").replace(/_/g, " ").toLowerCase();
const hourLabel = (h: number) => `${pad(h)}:00`;

/** Average packets heard in each hour of the day (this computer's local time). Hours with nothing heard count as zero. */
function busiest(hours: [number, number][]): number[] {
  if (hours.length === 0) return Array(24).fill(0);
  const first = hours[0][0], last = hours[hours.length - 1][0];
  const sum = Array(24).fill(0), buckets = Array(24).fill(0);
  const have = new Map(hours);
  for (let h = first; h <= last; h++) { const lh = new Date(h * HOUR_MS).getHours(); buckets[lh]++; sum[lh] += have.get(h) ?? 0; }
  return sum.map((s, i) => (buckets[i] ? s / buckets[i] : 0));
}

function BusiestChart({ values }: { values: number[] }) {
  const max = Math.max(1, ...values), top = values.indexOf(Math.max(...values));
  const W = 640, H = 170, L = 34, B = 20, bw = (W - L) / 24;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={`Average packets per hour of the day. The busiest hour is ${hourLabel(top)}.`}>
      {[0, 0.5, 1].map((f) => <g key={f}><line x1={L} x2={W} y1={H - B - f * (H - B - 8)} y2={H - B - f * (H - B - 8)} stroke="var(--line)" /><text x={L - 4} y={H - B - f * (H - B - 8) + 4} textAnchor="end" fontSize="10" fill="var(--muted)">{(max * f).toFixed(max < 10 ? 1 : 0)}</text></g>)}
      {values.map((v, h) => {
        const bh = (v / max) * (H - B - 8);
        return <g key={h}><rect x={L + h * bw + 2} y={H - B - bh} width={bw - 4} height={Math.max(bh, 0)} rx="2" fill={h === top && v > 0 ? "var(--amber)" : "var(--teal)"}><title>{`${hourLabel(h)}: ${v.toFixed(1)} packets per hour on average`}</title></rect>{h % 3 === 0 && <text x={L + h * bw + bw / 2} y={H - 6} textAnchor="middle" fontSize="10" fill="var(--muted)">{pad(h)}</text>}</g>;
      })}
    </svg>
  );
}

function Heatmap({ hours }: { hours: [number, number][] }) {
  const { days, grid, max } = useMemo(() => {
    const g = new Map<string, number[]>();
    let mx = 1;
    for (const [h, n] of hours) { const key = localDate(h), row = g.get(key) ?? Array(24).fill(null); row[new Date(h * HOUR_MS).getHours()] = n; g.set(key, row); mx = Math.max(mx, n); }
    const keys = [...g.keys()].sort().slice(-7);
    return { days: keys, grid: g, max: mx };
  }, [hours]);
  if (days.length === 0) return <p className="text-sm text-muted">Nothing recorded yet.</p>;
  const cw = 22, ch = 20, L = 74;
  return (
    <svg viewBox={`0 0 ${L + 24 * cw} ${days.length * ch + 22}`} className="w-full" role="img" aria-label="Packets heard in each hour over the last days. Darker is busier; grey means nothing was recorded.">
      {days.map((d, r) => (
        <g key={d}>
          <text x={L - 6} y={r * ch + 14} textAnchor="end" fontSize="10" fill="var(--muted)">{new Date(d + "T12:00").toLocaleDateString([], { weekday: "short", day: "numeric" })}</text>
          {grid.get(d)!.map((n, h) => <rect key={h} x={L + h * cw + 1} y={r * ch + 1} width={cw - 2} height={ch - 2} rx="2" fill={n === null ? "var(--line)" : "var(--amber)"} fillOpacity={n === null ? 0.35 : 0.12 + 0.88 * (n / max)}><title>{`${d} ${hourLabel(h)}: ${n === null ? "nothing recorded" : `${n} packets`}`}</title></rect>)}
        </g>
      ))}
      {[0, 6, 12, 18].map((h) => <text key={h} x={L + h * cw + cw / 2} y={days.length * ch + 14} textAnchor="middle" fontSize="10" fill="var(--muted)">{pad(h)}</text>)}
    </svg>
  );
}

const HOP_COLORS = ["var(--teal)", "#7aa2ff", "var(--amber)", "#f2545b"];
const HOP_LABELS = ["heard directly", "1 relay", "2 relays", "3 or more relays"];

function Hops({ trend, daily }: { trend: MeshHopTrend; daily: boolean }) {
  const buckets = useMemo(() => {
    const m = new Map<string, number[]>();
    for (const [hour, hops, n] of trend.hours) { const key = daily ? localDate(hour) : String(hour), a = m.get(key) ?? [0, 0, 0, 0]; a[Math.min(hops, 3)] += n; m.set(key, a); }
    return [...m.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1));
  }, [trend, daily]);
  if (buckets.length === 0) return <p className="text-sm text-muted">Nothing recorded yet.</p>;
  const W = 640, H = 170, L = 34, B = 22, bw = (W - L) / buckets.length;
  const max = Math.max(1, ...buckets.map(([, a]) => a.reduce((x, y) => x + y, 0)));
  const label = (key: string) => daily ? new Date(key + "T12:00").toLocaleDateString([], { month: "short", day: "numeric" }) : hourLabel(new Date(Number(key) * HOUR_MS).getHours());
  const every = Math.max(1, Math.ceil(buckets.length / 8));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Packets heard, by how many relays they passed through.">
      {[0, 0.5, 1].map((f) => <g key={f}><line x1={L} x2={W} y1={H - B - f * (H - B - 8)} y2={H - B - f * (H - B - 8)} stroke="var(--line)" /><text x={L - 4} y={H - B - f * (H - B - 8) + 4} textAnchor="end" fontSize="10" fill="var(--muted)">{Math.round(max * f)}</text></g>)}
      {buckets.map(([key, a], i) => {
        let y = H - B;
        return <g key={key}>{a.map((n, k) => { const bh = (n / max) * (H - B - 8); y -= bh; return bh > 0 ? <rect key={k} x={L + i * bw + 1} y={y} width={Math.max(bw - 2, 1)} height={bh} fill={HOP_COLORS[k]}><title>{`${label(key)}: ${n} ${HOP_LABELS[k]}`}</title></rect> : null; })}{i % every === 0 && <text x={L + i * bw + bw / 2} y={H - 6} textAnchor="middle" fontSize="10" fill="var(--muted)">{label(key)}</text>}</g>;
      })}
    </svg>
  );
}

export function Trends() {
  const [kind, setKind] = useState("");
  const [range, setRange] = useState(7);
  const packets = usePoll(() => api.get<MeshPacketTrend>(`/api/mesh/trends/packets?days=14${kind ? `&kind=${encodeURIComponent(kind)}` : ""}`), 60000, true, [kind]);
  const hops = usePoll(() => api.get<MeshHopTrend>(`/api/mesh/trends/hops?days=${range}`), 60000, true, [range]);
  const values = useMemo(() => busiest(packets.data?.hours ?? []), [packets.data]);
  const busyHour = values.indexOf(Math.max(...values));
  const spanH = packets.data && packets.data.hours.length ? packets.data.hours[packets.data.hours.length - 1][0] - packets.data.hours[0][0] + 1 : 0;
  const recent = useMemo(() => { const cut = Math.floor(Date.now() / HOUR_MS) - 7 * 24; return (packets.data?.hours ?? []).filter(([h]) => h >= cut); }, [packets.data]);
  const s = hops.data?.summary;
  const nothing = packets.data && packets.data.hours.length === 0 && !kind;

  return (
    <>
      <PageHeader title="Trends" subtitle="When the mesh is busy and how far packets travel. Counts only: nothing here records what a message said." />
      {(packets.error && !packets.data) && <p role="alert" className="mb-4 text-danger">{errorText(packets.error)}</p>}
      {nothing ? (
        <Card><EmptyState title="Nothing recorded yet">These charts fill in as the radio hears packets, and get better with every day that passes. The Nodes page can load a made-up mesh to show how they look.</EmptyState></Card>
      ) : (
        <>
          <Card className="mb-5">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <SectionTitle hint="Average packets heard in each hour of the day, in your local time, over the last 14 days (fewer if recording started later).">When is the mesh busiest?</SectionTitle>
              <Field label="Kind of packet" htmlFor="tr-kind"><select id="tr-kind" className="field" value={kind} onChange={(e) => setKind(e.target.value)}><option value="">Everything</option>{(packets.data?.kinds ?? []).map(([k, n]) => <option key={k} value={k}>{kindName(k)} ({n})</option>)}</select></Field>
            </div>
            <BusiestChart values={values} />
            {Math.max(...values) > 0 && <p className="mt-1 text-sm text-muted">Busiest around {hourLabel(busyHour)}, with about {values[busyHour].toFixed(1)} packets an hour.{spanH < 48 ? ` Only ${spanH} hour${spanH === 1 ? "" : "s"} have been recorded so far, so treat this as a first look: it gets more meaningful with every day that passes.` : ""}</p>}
          </Card>
          <Card className="mb-5">
            <SectionTitle hint="Each square is one hour of one day. Darker means more packets; grey means nothing was recorded (the node was off, or recording had not started).">The last week, hour by hour</SectionTitle>
            <Heatmap hours={recent} />
          </Card>
          <Card>
            <div className="flex flex-wrap items-end justify-between gap-3">
              <SectionTitle hint="How many relays each packet had passed through when your radio heard it. A packet that arrived directly says nothing about relays; the signal of a relayed one describes the last relay, not its sender.">How far do packets travel?</SectionTitle>
              <Field label="Period" htmlFor="tr-range"><select id="tr-range" className="field" value={range} onChange={(e) => setRange(Number(e.target.value))}><option value={1}>Last 24 hours</option><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option></select></Field>
            </div>
            {hops.data && <Hops trend={hops.data} daily={range > 1} />}
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm">{HOP_LABELS.map((l, i) => <span key={l} className="inline-flex items-center gap-1.5"><span className="inline-block h-3 w-3 rounded-sm" style={{ background: HOP_COLORS[i] }} />{l}</span>)}</div>
            {s && s.packets > 0 && <p className="mt-2 text-sm text-muted">{s.packets.toLocaleString()} packets: {Math.round((s.share_direct ?? 0) * 100)}% heard directly, {s.average_relays} relays on average, the furthest passed through {s.furthest}.</p>}
          </Card>
        </>
      )}
    </>
  );
}
