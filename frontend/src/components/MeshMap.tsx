import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "./ui";

export interface MapNode { id: string; label: string; short: string; lat: number; lon: number; tone: "fresh" | "day" | "old"; us: boolean; direct: boolean }
export interface MapTrail { id: string; points: [number, number][] }
/** A traceroute drawn on the map: hops in order (lat, lon, or null when that hop has no known position). `back` is dashed. */
export interface MapRoute { id: string; hops: ({ lat: number; lon: number; label: string } | null)[]; back?: boolean }
/** A direct link from this radio to a neighbour, coloured by signal quality and thicker the more it was heard. */
export interface MapLink { id: string; from: [number, number]; to: [number, number]; snr: number; packets: number }

/** Signal quality colour: good (teal) through marginal (amber) to poor (red). Never the only cue: the legend and the table say the numbers. */
export const snrColor = (snr: number) => snr >= 5 ? "#2dd4bf" : snr >= 0 ? "#9bd65f" : snr >= -5 ? "#ffb020" : snr >= -10 ? "#ff8a3d" : "#f2545b";

const TONE = { fresh: "var(--teal)", day: "var(--amber)", old: "var(--muted)" } as const;
const mx = (lon: number) => (lon + 180) / 360;
const my = (lat: number) => { const s = Math.sin((Math.max(-85, Math.min(85, lat)) * Math.PI) / 180); return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI); };
const lonOf = (x: number) => x * 360 - 180;
const latOf = (y: number) => (Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) * 180) / Math.PI;
const MIN_Z = 1, MAX_Z = 19;
const STEPS = [0.0001, 0.0002, 0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30];

interface View { x: number; y: number; z: number }  // x, y: the world point at the centre (0..1); z: zoom, 256 * 2^z pixels across the world

function fitView(nodes: MapNode[], w: number, h: number): View {
  if (nodes.length === 0) return { x: 0.5, y: 0.4, z: 2 };
  const xs = nodes.map((n) => mx(n.lon)), ys = nodes.map((n) => my(n.lat));
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const dx = Math.max(x1 - x0, 1e-6) * 1.3, dy = Math.max(y1 - y0, 1e-6) * 1.3;
  const z = Math.log2(Math.min(w / dx, h / dy) / 256);
  return { x: (x0 + x1) / 2, y: (y0 + y1) / 2, z: Math.max(MIN_Z, Math.min(MAX_Z - 3, z)) };
}

/** A plain offline map: nodes on a latitude/longitude grid (Web Mercator) with a scale bar, pan, zoom and optional movement trails.
 *  There is no background picture on purpose: LoRaPedia works without internet, and a background would have to be fetched from one. */
export function MeshMap({ nodes, trails, routes = [], links = [], selected, onSelect, formatDistance, fitTo }: {
  nodes: MapNode[]; trails: MapTrail[]; routes?: MapRoute[]; links?: MapLink[]; selected: string | null; onSelect: (id: string | null) => void;
  formatDistance: (km: number) => string; fitTo?: MapNode[];
}) {
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 640, h: 420 });
  const [view, setView] = useState<View | null>(null);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number; moved: boolean } | null>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: Math.max(240, el.clientWidth), h: Math.max(280, Math.min(560, Math.round(window.innerHeight * 0.62))) }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const fitted = useMemo(() => fitView(fitTo ?? nodes, size.w, size.h), [fitTo, nodes, size.w, size.h]);
  const v = view ?? fitted;
  const px = (lon: number) => (mx(lon) - v.x) * 256 * 2 ** v.z + size.w / 2;
  const py = (lat: number) => (my(lat) - v.y) * 256 * 2 ** v.z + size.h / 2;

  const zoomAt = useCallback((factor: number, sx: number, sy: number) => setView((cur) => {
    const c = cur ?? fitted, scale = 256 * 2 ** c.z;
    const z = Math.max(MIN_Z, Math.min(MAX_Z, c.z + Math.log2(factor)));
    const wx = c.x + (sx - size.w / 2) / scale, wy = c.y + (sy - size.h / 2) / scale;  // the world point under the pointer stays under it
    const s2 = 256 * 2 ** z;
    return { x: wx - (sx - size.w / 2) / s2, y: wy - (sy - size.h / 2) / s2, z };
  }), [fitted, size.w, size.h]);

  // The wheel must not also scroll the page, which React's own (passive) onWheel cannot prevent.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const on = (e: WheelEvent) => { e.preventDefault(); const r = el.getBoundingClientRect(); zoomAt(e.deltaY < 0 ? 1.4 : 1 / 1.4, e.clientX - r.left, e.clientY - r.top); };
    el.addEventListener("wheel", on, { passive: false });
    return () => el.removeEventListener("wheel", on);
  }, [zoomAt]);

  const scale = 256 * 2 ** v.z;
  const west = lonOf(v.x - size.w / 2 / scale), east = lonOf(v.x + size.w / 2 / scale);
  const north = latOf(v.y - size.h / 2 / scale), south = latOf(v.y + size.h / 2 / scale);
  const gridStep = STEPS.find((s) => (s / 360) * scale >= 90) ?? 30;
  const lons: number[] = [], lats: number[] = [];
  for (let l = Math.ceil(west / gridStep) * gridStep; l <= east && lons.length < 40; l += gridStep) lons.push(+l.toFixed(6));
  for (let l = Math.ceil(south / gridStep) * gridStep; l <= north && lats.length < 40; l += gridStep) lats.push(+l.toFixed(6));
  const dec = Math.max(0, Math.min(5, Math.ceil(-Math.log10(gridStep))));

  const centreLat = latOf(v.y);
  const mPerPx = (40075016.686 * Math.cos((centreLat * Math.PI) / 180)) / scale;
  const target = mPerPx * 110;
  const mag = 10 ** Math.floor(Math.log10(target)), r = target / mag;
  const barM = (r < 2 ? 1 : r < 5 ? 2 : 5) * mag;

  const showAllLabels = nodes.length <= 20 || v.z >= 12;
  const down = (e: React.PointerEvent) => { (e.currentTarget as Element).setPointerCapture(e.pointerId); drag.current = { x: e.clientX, y: e.clientY, vx: v.x, vy: v.y, moved: false }; };
  const move = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x, dy = e.clientY - d.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) d.moved = true;
    if (d.moved) setView({ x: d.vx - dx / scale, y: d.vy - dy / scale, z: v.z });
  };
  const up = () => { const moved = drag.current?.moved; drag.current = null; if (!moved) onSelect(null); };
  const key = (e: React.KeyboardEvent) => {
    const step = 60 / scale;
    if (e.key === "ArrowLeft") setView({ ...v, x: v.x - step });
    else if (e.key === "ArrowRight") setView({ ...v, x: v.x + step });
    else if (e.key === "ArrowUp") setView({ ...v, y: v.y - step });
    else if (e.key === "ArrowDown") setView({ ...v, y: v.y + step });
    else if (e.key === "+" || e.key === "=") zoomAt(1.5, size.w / 2, size.h / 2);
    else if (e.key === "-") zoomAt(1 / 1.5, size.w / 2, size.h / 2);
    else return;
    e.preventDefault();
  };

  return (
    <div ref={box} className="relative select-none overflow-hidden rounded-xl border border-line bg-surface" style={{ height: size.h }}>
      <svg width={size.w} height={size.h} role="application" aria-label="Map of nodes. Drag to move, scroll or use plus and minus to zoom, arrow keys to pan." tabIndex={0}
        className="touch-none cursor-grab outline-offset-2 active:cursor-grabbing" onPointerDown={down} onPointerMove={move} onPointerUp={up} onKeyDown={key}
        onDoubleClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); zoomAt(2, e.clientX - r.left, e.clientY - r.top); }}>
        {lons.map((l) => <g key={`lo${l}`}><line x1={px(l)} x2={px(l)} y1={0} y2={size.h} stroke="var(--line)" /><text x={px(l) + 4} y={size.h - 6} fontSize="10" fill="var(--muted)">{l.toFixed(dec)}°</text></g>)}
        {lats.map((l) => <g key={`la${l}`}><line x1={0} x2={size.w} y1={py(l)} y2={py(l)} stroke="var(--line)" /><text x={4} y={py(l) - 3} fontSize="10" fill="var(--muted)">{l.toFixed(dec)}°</text></g>)}
        {trails.map((t) => (
          <polyline key={t.id} fill="none" stroke="var(--amber)" strokeOpacity={0.55} strokeWidth="2" strokeLinejoin="round" strokeDasharray="5 4"
            points={t.points.map(([lat, lon]) => `${px(lon).toFixed(1)},${py(lat).toFixed(1)}`).join(" ")} />
        ))}
        {links.map((l) => <line key={l.id} x1={px(l.from[1])} y1={py(l.from[0])} x2={px(l.to[1])} y2={py(l.to[0])} stroke={snrColor(l.snr)} strokeOpacity={0.85} strokeWidth={1.5 + Math.min(4, Math.log2(Math.max(1, l.packets)) / 2)} strokeLinecap="round"><title>{`${l.id}: signal ${l.snr.toFixed(1)} dB, heard ${l.packets} times`}</title></line>)}
        {routes.map((r) => {
          const segs: string[] = [], gaps: string[] = [];
          let prev: { lat: number; lon: number } | null = null, hole = false;
          for (const h of r.hops) {
            if (h === null) { hole = true; continue; }
            if (prev) (hole ? gaps : segs).push(`M${px(prev.lon).toFixed(1)},${py(prev.lat).toFixed(1)} L${px(h.lon).toFixed(1)},${py(h.lat).toFixed(1)}`);
            prev = h; hole = false;
          }
          return (
            <g key={r.id}>
              <path d={segs.join(" ")} fill="none" stroke={r.back ? "var(--teal)" : "var(--amber)"} strokeWidth="3.5" strokeLinecap="round" strokeDasharray={r.back ? "8 5" : undefined} strokeOpacity={0.9} />
              <path d={gaps.join(" ")} fill="none" stroke={r.back ? "var(--teal)" : "var(--amber)"} strokeWidth="2" strokeDasharray="2 6" strokeLinecap="round" strokeOpacity={0.7} />
              {!r.back && r.hops.map((h, i) => h && <g key={i}><circle cx={px(h.lon)} cy={py(h.lat)} r="9" fill="var(--bg)" stroke="var(--amber)" strokeWidth="2" /><text x={px(h.lon)} y={py(h.lat) + 4} fontSize="11" textAnchor="middle" fill="var(--ink)">{i}</text><title>{h.label}</title></g>)}
            </g>
          );
        })}
        {nodes.map((n) => {
          const x = px(n.lon), y = py(n.lat);
          if (x < -20 || y < -20 || x > size.w + 20 || y > size.h + 20) return null;
          const sel = n.id === selected;
          return (
            <g key={n.id} role="button" tabIndex={0} aria-label={`${n.label}${n.us ? " (this radio)" : ""}`} aria-pressed={sel} className="cursor-pointer outline-none focus-visible:[&>circle:first-child]:stroke-[var(--ink)]"
              onPointerDown={(e) => e.stopPropagation()} onClick={(e) => { e.stopPropagation(); onSelect(sel ? null : n.id); }}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(sel ? null : n.id); } }}>
              <circle cx={x} cy={y} r={sel ? 11 : n.us ? 9 : 7} fill={n.us ? "none" : TONE[n.tone]} stroke={n.us || sel ? "var(--ink)" : n.direct ? "var(--ink)" : "var(--bg)"} strokeWidth={n.us || sel ? 3 : n.direct ? 2 : 1.5} />
              {n.us && <circle cx={x} cy={y} r={3} fill="var(--ink)" />}
              {(showAllLabels || sel || n.us) && <text x={x + 12} y={y + 4} fontSize="12" fill="var(--ink)" stroke="var(--surface)" strokeWidth="3" paintOrder="stroke">{n.short}</text>}
            </g>
          );
        })}
        <g transform={`translate(${size.w - 130},${size.h - 24})`}>
          <line x1="0" x2={barM / mPerPx} y1="0" y2="0" stroke="var(--ink)" strokeWidth="2" /><line x1="0" x2="0" y1="-4" y2="4" stroke="var(--ink)" strokeWidth="2" /><line x1={barM / mPerPx} x2={barM / mPerPx} y1="-4" y2="4" stroke="var(--ink)" strokeWidth="2" />
          <text x="0" y="-8" fontSize="11" fill="var(--ink)" stroke="var(--surface)" strokeWidth="3" paintOrder="stroke">{formatDistance(barM / 1000)}</text>
        </g>
      </svg>
      <div className="absolute right-2 top-2 flex flex-col gap-1.5">
        <Button aria-label="Zoom in" onClick={() => zoomAt(1.6, size.w / 2, size.h / 2)}>+</Button>
        <Button aria-label="Zoom out" onClick={() => zoomAt(1 / 1.6, size.w / 2, size.h / 2)}>−</Button>
        <Button aria-label="Fit every node" onClick={() => setView(null)}>Fit</Button>
      </div>
    </div>
  );
}
