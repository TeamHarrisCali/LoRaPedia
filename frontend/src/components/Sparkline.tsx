/** Minimal area sparkline, e.g. queries per hour for the last 24 hours. */
export function Sparkline({ data, label, height = 64 }: { data: number[]; label: string; height?: number }) {
  const w = 480, pad = 4;
  const max = Math.max(1, ...data);
  const x = (i: number) => pad + (i / Math.max(1, data.length - 1)) * (w - pad * 2);
  const y = (v: number) => height - pad - (v / max) * (height - pad * 2);
  const line = data.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join("");
  const area = `${line}L${x(data.length - 1).toFixed(1)} ${height}L${x(0).toFixed(1)} ${height}Z`;
  return (
    <svg viewBox={`0 0 ${w} ${height}`} className="w-full" style={{ height }} role="img" aria-label={label} preserveAspectRatio="none">
      <path d={area} fill="var(--amber)" opacity="0.15" />
      <path d={line} fill="none" stroke="var(--amber)" strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  );
}
