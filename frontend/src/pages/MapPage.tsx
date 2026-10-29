import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { MAP_TILES_NOTE } from "../components/MapTilesCard";
import { MeshMap, snrColor, type MapLink, type MapNode } from "../components/MeshMap";
import { Badge, Button, Card, EmptyState, PageHeader, Toggle } from "../components/ui";
import { api, errorText } from "../lib/api";
import { timeAgo } from "../lib/format";
import { useSettings } from "../lib/settings";
import type { MeshLinks, MeshNode, MeshNodes, MeshTrails } from "../lib/types";
import { distanceKm, fmtDistance, useUnits } from "../lib/units";
import { usePoll } from "../lib/usePoll";

const WITHIN = [["any", "Any time", Infinity], ["week", "Last 7 days", 7 * 86400], ["day", "Last 24 hours", 86400], ["hour", "Last hour", 3600]] as const;

export function MapPage() {
  const units = useUnits();
  const { settings, save } = useSettings();
  const [asking, setAsking] = useState(false);          // the "this will contact OpenStreetMap" question, before the street map is first turned on
  const street = !!settings?.map_tiles;
  async function setStreet(on: boolean) { if (settings && await save({ ...settings, map_tiles: on })) setAsking(false); }
  const data = usePoll(() => api.get<MeshNodes>("/api/mesh/nodes"), 10000);
  const [showTrails, setShowTrails] = useState(false);
  const trails = usePoll(() => api.get<MeshTrails>("/api/mesh/trails?days=7"), 60000, showTrails);
  const [showLinks, setShowLinks] = useState(false);
  const linkData = usePoll(() => api.get<MeshLinks>("/api/mesh/links?days=7"), 60000, showLinks);
  const [within, setWithin] = useState<(typeof WITHIN)[number][0]>("any");
  const [remembered, setRemembered] = useState(true);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const all = data.data?.nodes ?? [];
  const limit = WITHIN.find((w) => w[0] === within)![2];
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return all.filter((n) => n.lat !== null && (remembered || n.live) && (limit === Infinity || (n.age_s !== null && n.age_s <= limit))
      && (!needle || [n.display, n.name, n.short, n.id].some((v) => v?.toLowerCase().includes(needle))));
  }, [all, remembered, limit, q]);
  const placeless = all.filter((n) => n.lat === null);
  const us = all.find((n) => n.us && n.lat !== null);
  const mapNodes: MapNode[] = shown.map((n) => ({
    id: n.id, label: n.display, short: n.short ?? n.display.slice(0, 6), lat: n.lat!, lon: n.lon!, us: n.us, direct: n.hops_measured,
    tone: n.age_s !== null && n.age_s <= 3600 ? "fresh" : n.age_s !== null && n.age_s <= 86400 ? "day" : "old",
  }));
  const trailLines = showTrails ? Object.entries(trails.data?.trails ?? {}).filter(([id]) => shown.some((n) => n.id === id)).map(([id, pts]) => ({ id, points: pts.map(([lat, lon]) => [lat, lon] as [number, number]) })) : [];
  const mapLinks: MapLink[] = showLinks && linkData.data?.us
    ? linkData.data.links.filter((l) => l.lat !== null && shown.some((n) => n.id === l.id))
      .map((l) => ({ id: l.id, from: [linkData.data!.us!.lat, linkData.data!.us!.lon] as [number, number], to: [l.lat!, l.lon!] as [number, number], snr: l.snr_avg, packets: l.packets })) : [];
  const sel: MeshNode | undefined = all.find((n) => n.id === selected);

  return (
    <>
      <PageHeader title="Map" subtitle="Every node with a known position. The plain grid works without the internet; turn on the street map to see roads and towns behind the nodes." />
      {data.error && !data.data && <p role="alert" className="mb-4 text-danger">{errorText(data.error)}</p>}
      {data.data && all.length === 0 ? (
        <Card><EmptyState title="Nothing to show yet">Nodes appear here once the radio has heard them and they share a position. Add a made-up mesh on the Nodes page to see how this works.</EmptyState></Card>
      ) : data.data && (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2">
            <input aria-label="Search nodes" type="search" className="field max-w-xs" placeholder="Search name or id…" value={q} onChange={(e) => setQ(e.target.value)} />
            <select aria-label="Heard within" className="field max-w-[12rem]" value={within} onChange={(e) => setWithin(e.target.value as typeof within)}>{WITHIN.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
            <Toggle checked={remembered} onChange={setRemembered} label="Include nodes the radio has forgotten" />
            <Toggle checked={showTrails} onChange={setShowTrails} label="Show movement trails" />
            <Toggle checked={showLinks} onChange={setShowLinks} label="Show link quality" />
            <Toggle checked={street || asking} onChange={(v) => { if (!v) { setAsking(false); void setStreet(false); } else if (street) void setStreet(true); else setAsking(true); }} label="Street map" />
          </div>
          {asking && !street && (
            <Card className="mb-3 border-amber/50" as="div">
              <p className="font-serif text-lg font-semibold">Show a street map?</p>
              <p className="mt-1 text-sm text-muted">{MAP_TILES_NOTE}</p>
              <div className="mt-3 flex flex-wrap gap-2"><Button variant="primary" onClick={() => void setStreet(true)}>Turn on the street map</Button><Button onClick={() => setAsking(false)}>Not now</Button></div>
            </Card>
          )}
          {shown.length === 0 ? <Card><p className="text-sm text-muted">No node with a position matches.</p></Card> : (
            <MeshMap tiles={street} nodes={mapNodes} trails={trailLines} links={mapLinks} selected={selected} onSelect={setSelected} formatDistance={(km) => fmtDistance(km, units.distance)} />
          )}
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted">
            <span className="inline-flex items-center gap-1.5"><span className="inline-block h-3 w-3 rounded-full" style={{ background: "var(--teal)" }} />heard in the last hour</span>
            <span className="inline-flex items-center gap-1.5"><span className="inline-block h-3 w-3 rounded-full" style={{ background: "var(--amber)" }} />last 24 hours</span>
            <span className="inline-flex items-center gap-1.5"><span className="inline-block h-3 w-3 rounded-full" style={{ background: "var(--muted)" }} />older</span>
            <span className="inline-flex items-center gap-1.5"><span className="inline-block h-3 w-3 rounded-full border-2" style={{ borderColor: "var(--ink)" }} />heard directly, no relay</span>
            <span className="inline-flex items-center gap-1.5"><span className="inline-block h-3 w-3 rounded-full border-[3px]" style={{ borderColor: "var(--ink)" }} />this radio</span>
            {showLinks && <span className="inline-flex items-center gap-1.5">links: {[[5, "5 dB or better"], [0, "0 to 5"], [-5, "-5 to 0"], [-10, "-10 to -5"], [-15, "weaker"]].map(([v, l]) => <span key={String(v)} className="inline-flex items-center gap-1"><span className="inline-block h-1 w-4 rounded" style={{ background: snrColor(Number(v)) }} />{l}</span>)}</span>}
            {showLinks && linkData.data && !linkData.data.us && <span className="text-amber-text">this radio has no known position, so no links can be drawn</span>}
            <span>{shown.length} on the map{placeless.length ? `, ${placeless.length} with no known position` : ""}</span>
          </div>
          {sel && (
            <Card className="mt-3" as="div">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="font-display text-lg">{sel.display}</h2>
                {sel.us && <Badge tone="teal">this radio</Badge>}
                <Badge tone={sel.live ? "teal" : "muted"}>{sel.live ? "in the radio's list" : "remembered"}</Badge>
                <Link className="ml-auto text-sm underline" to={`/nodes/${encodeURIComponent(sel.id)}`}>Open its page</Link>
              </div>
              <p className="mt-1 text-sm text-muted">
                {sel.id} · {sel.hops === null ? "distance unknown" : sel.hops === 0 ? "heard directly" : `${sel.hops} relay${sel.hops > 1 ? "s" : ""} away`} · last heard {sel.last_heard ? timeAgo(sel.last_heard) : "never"}
                {us && sel.lat !== null && !sel.us ? ` · ${fmtDistance(distanceKm(us.lat!, us.lon!, sel.lat, sel.lon!), units.distance)} from this radio` : ""}
              </p>
              <p className="mt-1 text-sm text-muted">Position: {sel.lat?.toFixed(5)}, {sel.lon?.toFixed(5)} ({sel.pos_source ?? "?"}, {timeAgo(sel.pos_ts)})</p>
            </Card>
          )}
          {placeless.length > 0 && (
            <details className="mt-3 text-sm"><summary className="cursor-pointer text-muted">Nodes with no known position ({placeless.length})</summary>
              <ul className="mt-2 grid gap-1 sm:grid-cols-2">{placeless.map((n) => <li key={n.id}><Link className="underline" to={`/nodes/${encodeURIComponent(n.id)}`}>{n.display}</Link> <span className="text-muted">{n.last_heard ? timeAgo(n.last_heard) : "never heard"}</span></li>)}</ul>
            </details>
          )}
        </>
      )}
    </>
  );
}
