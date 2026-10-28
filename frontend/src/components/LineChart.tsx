import { useMemo, useState } from "react";

export interface Line { id: string; label: string; points: [number, number][]; color: string }

/** The colours of the lines: distinguishable in both themes, and never the only cue (the legend names each line). */
export const LINE_COLORS = ["#2dd4bf", "#ffb020", "#f2545b", "#7aa2ff", "#b58cff", "#9bd65f", "#ff8fb1", "#e0b25a"];

const W = 640, H = 220, L = 46, R = 10, T = 10, B = 24;

function niceTicks(lo: number, hi: number, n = 4): number[] {
  if (hi === lo) return [lo];
  const step0 = (hi - lo) / n, mag = 10 ** Math.floor(Math.log10(step0)), r = step0 / mag;
  const step = (r < 1.5 ? 1 : r < 3 ? 2 : r < 7 ? 5 : 10) * mag;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

/** A small multi-line time chart with a legend and a hover read-out. Points are [unix seconds, value]. */
export function LineChart({ lines, unit = "", digits = 1, label, formatValue }: { lines: Line[]; unit?: string; digits?: number; label: string; formatValue?: (v: number) => string }) {
  const [hover, setHover] = useState<{ line: Line; point: [number, number] } | null>(null);
  const geo = useMemo(() => {
    const all = lines.flatMap((l) => l.points);
    if (all.length === 0) return null;
    const xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
    let x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    if (x0 === x1) { x0 -= 1800; x1 += 1800; }
    const pad = (y1 - y0) * 0.08 || Math.abs(y1) * 0.05 || 1;
    y0 -= pad; y1 += pad;
    return { x0, x1, y0, y1, X: (x: number) => L + ((x - x0) / (x1 - x0)) * (W - L - R), Y: (y: number) => T + (1 - (y - y0) / (y1 - y0)) * (H - T - B) };
  }, [lines]);
  const fmt = formatValue ?? ((v: number) => `${v.toFixed(digits)}${unit}`);
  if (!geo) return <p className="py-6 text-center text-sm text-muted">Nothing recorded in this period yet.</p>;
  const spanH = (geo.x1 - geo.x0) / 3600;
  const xTick = (t: number) => { const d = new Date(t * 1000); return spanH <= 36 ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : d.toLocaleDateString([], { month: "short", day: "numeric" }); };

  function onMove(e: React.PointerEvent<SVGSVGElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W, py = ((e.clientY - box.top) / box.height) * H;
    let best: { line: Line; point: [number, number]; d: number } | null = null;
    for (const line of lines) for (const p of line.points) {
      const d = (geo!.X(p[0]) - px) ** 2 + (geo!.Y(p[1]) - py) ** 2;
      if (!best || d < best.d) best = { line, point: p, d };
    }
    setHover(best && best.d < 40 ** 2 ? { line: best.line, point: best.point } : null);
  }

  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full touch-pan-y" role="img" aria-label={label} onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        {niceTicks(geo.y0, geo.y1).map((v) => (
          <g key={v}><line x1={L} x2={W - R} y1={geo.Y(v)} y2={geo.Y(v)} stroke="var(--line)" strokeWidth="1" /><text x={L - 6} y={geo.Y(v) + 4} textAnchor="end" fontSize="11" fill="var(--muted)">{+v.toFixed(2)}</text></g>
        ))}
        {niceTicks(geo.x0, geo.x1, 4).map((t) => <text key={t} x={geo.X(t)} y={H - 6} textAnchor="middle" fontSize="11" fill="var(--muted)">{xTick(t)}</text>)}
        {lines.map((l) => l.points.length > 0 && (
          <polyline key={l.id} fill="none" stroke={l.color} strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke"
            points={l.points.map((p) => `${geo.X(p[0]).toFixed(1)},${geo.Y(p[1]).toFixed(1)}`).join(" ")} />
        ))}
        {lines.map((l) => l.points.length <= 3 && l.points.map((p) => <circle key={`${l.id}-${p[0]}`} cx={geo.X(p[0])} cy={geo.Y(p[1])} r="3.5" fill={l.color} />))}
        {hover && <circle cx={geo.X(hover.point[0])} cy={geo.Y(hover.point[1])} r="4.5" fill={hover.line.color} stroke="var(--bg)" strokeWidth="2" />}
      </svg>
      <div className="mt-1 min-h-5 text-sm" aria-live="polite">
        {hover ? <span><b style={{ color: hover.line.color }}>{hover.line.label}</b>: {fmt(hover.point[1])} <span className="text-muted">at {new Date(hover.point[0] * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span></span> : <span className="text-muted">Move over a line to read a value.</span>}
      </div>
      <figcaption className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm">
        {lines.map((l) => <span key={l.id} className="inline-flex items-center gap-1.5"><span className="inline-block h-0.5 w-4" style={{ background: l.color }} />{l.label}</span>)}
      </figcaption>
    </figure>
  );
}
