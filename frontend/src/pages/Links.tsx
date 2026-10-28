import { Pager } from "../components/Pager";
import { usePaged } from "../lib/usePaged";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { snrColor } from "../components/MeshMap";
import { Card, EmptyState, Field, PageHeader, SectionTitle } from "../components/ui";
import { api, errorText } from "../lib/api";
import { timeAgo } from "../lib/format";
import type { MeshLink, MeshLinks } from "../lib/types";
import { fmtDistance, KM_PER_MILE, useUnits } from "../lib/units";
import { usePoll } from "../lib/usePoll";

/** About the weakest signal that can still be decoded on Meshtastic's default LongFast setting. Other settings differ a little. */
const DECODE_FLOOR_DB = -17.5;

function Scatter({ links, miles }: { links: MeshLink[]; miles: boolean }) {
  const pts = links.filter((l) => l.distance_km !== null);
  const W = 640, H = 260, L = 44, R = 12, T = 12, B = 30;
  const xmax = Math.max(1, ...pts.map((p) => p.distance_km! * (miles ? 1 / KM_PER_MILE : 1))) * 1.1;
  const lo = Math.min(DECODE_FLOOR_DB - 2, ...pts.map((p) => p.snr_min)), hi = Math.max(10, ...pts.map((p) => p.snr_max)) + 1;
  const X = (v: number) => L + (v / xmax) * (W - L - R), Y = (s: number) => T + (1 - (s - lo) / (hi - lo)) * (H - T - B);
  const yTicks = [-20, -15, -10, -5, 0, 5, 10].filter((t) => t >= lo && t <= hi);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Signal strength against distance for each node heard directly, with the weakest decodable signal marked.">
      {yTicks.map((t) => <g key={t}><line x1={L} x2={W - R} y1={Y(t)} y2={Y(t)} stroke="var(--line)" /><text x={L - 6} y={Y(t) + 4} textAnchor="end" fontSize="11" fill="var(--muted)">{t}</text></g>)}
      {[0, 0.25, 0.5, 0.75, 1].map((f) => <text key={f} x={X(xmax * f)} y={H - 10} textAnchor="middle" fontSize="11" fill="var(--muted)">{(xmax * f).toFixed(xmax < 10 ? 1 : 0)}</text>)}
      <text x={W - R} y={H - 1} textAnchor="end" fontSize="11" fill="var(--muted)">{miles ? "miles" : "km"} from this radio</text>
      <line x1={L} x2={W - R} y1={Y(DECODE_FLOOR_DB)} y2={Y(DECODE_FLOOR_DB)} stroke="var(--danger)" strokeDasharray="6 4" />
      <text x={W - R - 4} y={Y(DECODE_FLOOR_DB) - 5} textAnchor="end" fontSize="11" fill="var(--danger)">about the weakest decodable signal ({DECODE_FLOOR_DB} dB)</text>
      {pts.map((p) => {
        const x = X(p.distance_km! * (miles ? 1 / KM_PER_MILE : 1));
        return <g key={p.id}><line x1={x} x2={x} y1={Y(p.snr_min)} y2={Y(p.snr_max)} stroke={snrColor(p.snr_avg)} strokeWidth="2" strokeOpacity="0.6" /><circle cx={x} cy={Y(p.snr_avg)} r="5" fill={snrColor(p.snr_avg)} stroke="var(--bg)" strokeWidth="1.5"><title>{`${p.name}: average ${p.snr_avg} dB (worst ${p.snr_min}, best ${p.snr_max}), ${p.packets} packets`}</title></circle></g>;
      })}
    </svg>
  );
}

export function Links() {
  const units = useUnits();
  const [days, setDays] = useState(7);
  const data = usePoll(() => api.get<MeshLinks>(`/api/mesh/links?days=${days}`), 30000, true, [days]);
  const d = data.data;
  const links = d?.links ?? [];
  const sorted = useMemo(() => [...links].sort((a, b) => (b.distance_km ?? -1) - (a.distance_km ?? -1) || b.packets - a.packets), [links]);
  const linkPage = usePaged(sorted, 15);
  const best = links.length ? Math.max(...links.map((l) => l.snr_avg)) : null, worst = links.length ? Math.min(...links.map((l) => l.snr_avg)) : null;

  return (
    <>
      <PageHeader title="Link quality" subtitle="How well your radio hears the nodes that reach it directly, with no relay in between." />
      {data.error && !d && <p role="alert" className="mb-4 text-danger">{errorText(data.error)}</p>}
      <div className="mb-4 max-w-xs"><Field label="Period" htmlFor="lk-days"><select id="lk-days" className="field" value={days} onChange={(e) => setDays(Number(e.target.value))}><option value={1}>Last 24 hours</option><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option></select></Field></div>
      {d && links.length === 0 ? (
        <Card><EmptyState title="No direct neighbours recorded yet">A node counts here when your radio hears it with no relay in between. The signal of a relayed packet describes the relay, not the sender, so it is left out.</EmptyState></Card>
      ) : d && (
        <>
          <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-4">
            {[["Direct neighbours", String(links.length)], ["Farthest", d.farthest ? `${fmtDistance(d.farthest.distance_km, units.distance)}` : "–", d.farthest?.name],
              ["Best average signal", best === null ? "–" : `${best.toFixed(1)} dB`], ["Weakest average signal", worst === null ? "–" : `${worst.toFixed(1)} dB`]].map(([l, v, sub]) => (
              <div key={l} className="rounded-xl border border-line bg-surface p-4"><p className="text-sm text-muted">{l}</p><p className="mt-1 font-display text-2xl">{v}</p>{sub && <p className="mt-0.5 truncate text-xs text-muted">{sub}</p>}</div>
            ))}
          </div>
          <Card className="mb-5">
            <SectionTitle hint="Each dot is one node: its average signal, with a bar from its worst to its best. Nearer nodes are usually stronger. Dots near the red line are close to the limit of what the radio can decode.">Signal against distance</SectionTitle>
            {d.us ? <Scatter links={links} miles={units.distance === "mi"} /> : <p className="text-sm text-muted">This radio has no known position, so distances cannot be worked out. Give it one (a GPS fix or a fixed position in the Meshtastic app) and this fills in.</p>}
          </Card>
          <Card>
            <SectionTitle hint="Signal is the signal-to-noise ratio in dB: higher is better, and below about -15 is near the edge of what can be decoded.">Direct neighbours</SectionTitle>
            <div className="overflow-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-muted"><tr><th className="py-1 pr-3 font-normal">Node</th><th className="pr-3 font-normal">Signal</th><th className="pr-3 font-normal">Range</th><th className="pr-3 font-normal">RSSI</th><th className="pr-3 font-normal">Packets</th><th className="pr-3 font-normal">Distance</th><th className="font-normal">Last heard</th></tr></thead>
                <tbody>
                  {linkPage.slice.map((l) => (
                    <tr key={l.id} className="border-t border-line">
                      <td className="py-1 pr-3"><Link className="underline" to={`/nodes/${encodeURIComponent(l.id)}`}>{l.name}</Link></td>
                      <td className="pr-3"><span className="inline-block h-2.5 w-2.5 rounded-full align-middle" style={{ background: snrColor(l.snr_avg) }} aria-hidden /> {l.snr_avg} dB</td>
                      <td className="pr-3 text-muted">{l.snr_min} to {l.snr_max}</td><td className="pr-3">{l.rssi_avg} dBm</td><td className="pr-3">{l.packets}</td>
                      <td className="pr-3">{l.distance_km === null ? "–" : fmtDistance(l.distance_km, units.distance)}</td><td className="text-muted">{timeAgo(l.last)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager page={linkPage.page} pages={linkPage.pages} total={linkPage.total} size={linkPage.size} onPage={linkPage.setPage} noun="neighbours" />
          </Card>
        </>
      )}
    </>
  );
}
