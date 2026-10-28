import { Pager } from "../components/Pager";
import { usePaged } from "../lib/usePaged";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { LINE_COLORS, LineChart, type Line } from "../components/LineChart";
import { Card, EmptyState, Field, PageHeader, SectionTitle } from "../components/ui";
import { api, errorText } from "../lib/api";
import { timeAgo } from "../lib/format";
import type { MeshMetric, MeshNodes, MeshReading, MeshSensors, MeshSeries } from "../lib/types";
import { fmtTemp, useUnits } from "../lib/units";
import { usePoll } from "../lib/usePoll";

const METRICS: { key: MeshMetric; label: string; unit: string; digits: number }[] = [
  { key: "battery", label: "Battery", unit: "%", digits: 0 }, { key: "voltage", label: "Voltage", unit: " V", digits: 2 },
  { key: "channel_util", label: "Channel use", unit: "%", digits: 1 }, { key: "air_util_tx", label: "Airtime sending", unit: "%", digits: 1 },
  { key: "temperature", label: "Temperature", unit: "", digits: 1 }, { key: "humidity", label: "Humidity", unit: "%", digits: 0 },
  { key: "pressure", label: "Pressure", unit: " hPa", digits: 0 }, { key: "uptime_s", label: "Uptime", unit: " h", digits: 1 },
];
const RANGES = [[6, "Last 6 hours"], [24, "Last 24 hours"], [72, "Last 3 days"], [168, "Last 7 days"], [720, "Last 30 days"]] as const;
const WINDOWS = [[0.25, "15 minutes"], [1, "1 hour"], [6, "6 hours"], [24, "24 hours"]] as const;

export function Telemetry() {
  const units = useUnits();
  const [metric, setMetric] = useState<MeshMetric>("battery");
  const [hours, setHours] = useState(72);
  const [chosen, setChosen] = useState<string[]>([]);
  const [window, setWindow] = useState(1);
  const nodes = usePoll(() => api.get<MeshNodes>("/api/mesh/nodes"), 30000);
  const names = useMemo(() => Object.fromEntries((nodes.data?.nodes ?? []).map((n) => [n.id, n.display])), [nodes.data]);
  const reporters = (nodes.data?.nodes ?? []).filter((n) => n.metrics_ts);
  const ids = chosen.join(",");
  const series = usePoll(() => api.get<MeshSeries>(`/api/mesh/telemetry/series?metric=${metric}&hours=${hours}${ids ? `&nodes=${encodeURIComponent(ids)}` : ""}`), 30000, true, [metric, hours, ids]);
  const sensors = usePoll(() => api.get<MeshSensors>(`/api/mesh/sensors?hours=${window}`), 30000, true, [window]);
  const readings = usePoll(() => api.get<{ readings: MeshReading[] }>("/api/mesh/telemetry/readings?hours=24&limit=200"), 30000);
  const readingPage = usePaged(readings.data?.readings ?? [], 20);

  const meta = METRICS.find((m) => m.key === metric)!;
  const convert = (v: number) => metric === "temperature" && units.temperature === "F" ? v * 9 / 5 + 32 : metric === "uptime_s" ? v / 3600 : v;
  const unit = metric === "temperature" ? (units.temperature === "F" ? " °F" : " °C") : meta.unit;
  const lines: Line[] = Object.entries(series.data?.series ?? {}).map(([id, pts], i) => ({
    id, label: names[id] ?? id, color: LINE_COLORS[i % LINE_COLORS.length], points: pts.map(([t, v]) => [t, convert(v)] as [number, number]),
  }));
  const toggle = (id: string) => setChosen((c) => c.includes(id) ? c.filter((x) => x !== id) : c.length < 12 ? [...c, id] : c);
  const avg = sensors.data?.average;
  const empty = nodes.data && reporters.length === 0;

  return (
    <>
      <PageHeader title="Telemetry" subtitle="Battery, signal and sensor readings that other nodes broadcast and your radio hears, kept on this computer." />
      {(nodes.error && !nodes.data) && <p role="alert" className="mb-4 text-danger">{errorText(nodes.error)}</p>}
      {empty ? (
        <Card><EmptyState title="No readings heard yet">Nodes with the telemetry module switched on broadcast their battery and sensors every so often, and they appear here as the radio hears them. The Nodes page can load a made-up mesh to show how this looks.</EmptyState></Card>
      ) : (
        <>
          <Card className="mb-5">
            <div className="mb-2 flex flex-wrap items-end justify-between gap-3">
              <SectionTitle hint="Averaged over the sensor nodes that reported in the window. Each node counts once, however often it reports.">Sensors around you</SectionTitle>
              <select aria-label="Averaging window" className="field max-w-[10rem]" value={window} onChange={(e) => setWindow(Number(e.target.value))}>{WINDOWS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
            </div>
            {sensors.data && sensors.data.nodes.length === 0 ? <p className="text-sm text-muted">No sensor node has reported in this window.</p> : (
              <>
                <div className="grid grid-cols-3 gap-3 text-center">
                  <div><p className="text-sm text-muted">Temperature</p><p className="font-display text-2xl">{fmtTemp(avg?.temperature, units.temperature)}</p></div>
                  <div><p className="text-sm text-muted">Humidity</p><p className="font-display text-2xl">{avg?.humidity == null ? "–" : `${avg.humidity.toFixed(0)} %`}</p></div>
                  <div><p className="text-sm text-muted">Pressure</p><p className="font-display text-2xl">{avg?.pressure == null ? "–" : `${avg.pressure.toFixed(0)} hPa`}</p></div>
                </div>
                <ul className="mt-3 divide-y divide-line text-sm">
                  {sensors.data?.nodes.map((n) => (
                    <li key={n.id} className="flex flex-wrap gap-x-4 py-1.5"><Link className="underline" to={`/nodes/${encodeURIComponent(n.id)}`}>{n.name}</Link><span>{fmtTemp(n.temperature, units.temperature)}</span><span>{n.humidity == null ? "" : `${n.humidity.toFixed(0)} %`}</span><span>{n.pressure == null ? "" : `${n.pressure.toFixed(0)} hPa`}</span><span className="ml-auto text-muted">{n.readings} reading{n.readings > 1 ? "s" : ""}, last {timeAgo(n.last)}</span></li>
                  ))}
                </ul>
              </>
            )}
          </Card>

          <Card className="mb-5">
            <SectionTitle hint="One reading over time, for the nodes you pick (or, with none picked, the eight that reported most recently).">History</SectionTitle>
            <div className="grid gap-x-4 sm:grid-cols-2">
              <Field label="What" htmlFor="t-metric"><select id="t-metric" className="field" value={metric} onChange={(e) => setMetric(e.target.value as MeshMetric)}>{METRICS.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}</select></Field>
              <Field label="How far back" htmlFor="t-range"><select id="t-range" className="field" value={hours} onChange={(e) => setHours(Number(e.target.value))}>{RANGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
            </div>
            <details className="mb-3 text-sm">
              <summary className="cursor-pointer text-muted">Nodes: {chosen.length ? `${chosen.length} chosen` : "most recent reporters"}</summary>
              <div className="mt-2 grid gap-1 sm:grid-cols-2">
                {reporters.map((n) => <label key={n.id} className="flex items-center gap-2"><input type="checkbox" checked={chosen.includes(n.id)} onChange={() => toggle(n.id)} />{n.display}</label>)}
              </div>
              {chosen.length > 0 && <button type="button" className="mt-2 underline" onClick={() => setChosen([])}>Clear the choice</button>}
            </details>
            {series.error && !series.data ? <p role="alert" className="text-danger">{errorText(series.error)}</p> : <LineChart lines={lines} unit={unit} digits={meta.digits} label={`${meta.label} over time`} />}
          </Card>

          <Card>
            <div className="mb-2 flex flex-wrap items-end justify-between gap-2">
              <SectionTitle hint="The newest readings of the last 24 hours.">Recent readings</SectionTitle>
              <a className="inline-flex items-center rounded-lg border border-line bg-raised px-3.5 py-2 text-sm hover:border-muted" href="/api/mesh/telemetry/export.csv?days=30" download>Export 30 days (CSV)</a>
            </div>
            <div className="overflow-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-muted"><tr><th className="py-1 pr-3 font-normal">When</th><th className="pr-3 font-normal">Node</th><th className="pr-3 font-normal">Battery</th><th className="pr-3 font-normal">Voltage</th><th className="pr-3 font-normal">Channel</th><th className="pr-3 font-normal">Temp</th><th className="pr-3 font-normal">Humidity</th><th className="font-normal">Signal</th></tr></thead>
                <tbody>
                  {readingPage.slice.map((r, i) => (
                    <tr key={`${r.ts}-${r.node_id}-${i}`} className="border-t border-line">
                      <td className="py-1 pr-3 text-muted">{timeAgo(r.ts)}</td><td className="pr-3">{r.node_label ?? r.node_name ?? r.node_short ?? r.node_id}</td>
                      <td className="pr-3">{r.battery == null ? "" : r.battery > 100 ? "powered" : `${Math.round(r.battery)}%`}</td><td className="pr-3">{r.voltage == null ? "" : `${r.voltage.toFixed(2)} V`}</td>
                      <td className="pr-3">{r.channel_util == null ? "" : `${r.channel_util.toFixed(1)}%`}</td><td className="pr-3">{r.temperature == null ? "" : fmtTemp(r.temperature, units.temperature)}</td>
                      <td className="pr-3">{r.humidity == null ? "" : `${r.humidity.toFixed(0)}%`}</td><td>{r.snr == null ? "" : `${r.snr} dB`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager page={readingPage.page} pages={readingPage.pages} total={readingPage.total} size={readingPage.size} onPage={readingPage.setPage} noun="readings" />
          </Card>
        </>
      )}
    </>
  );
}
