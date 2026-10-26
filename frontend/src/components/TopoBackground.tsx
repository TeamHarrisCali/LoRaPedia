/** Faint topographic contour lines behind the page: two "hills" of nested, wobbling rings. */
function ring(cx: number, cy: number, base: number, k: number, phase: number): string {
  const pts: string[] = [];
  for (let i = 0; i <= 96; i++) {
    const a = (i / 96) * Math.PI * 2;
    const wobble = 1 + 0.11 * Math.sin(3 * a + phase) + 0.06 * Math.sin(5 * a + phase * 1.7) + 0.03 * Math.sin(8 * a + k);
    const r = base * k * wobble;
    pts.push(`${i ? "L" : "M"}${(cx + r * 1.35 * Math.cos(a)).toFixed(1)} ${(cy + r * Math.sin(a)).toFixed(1)}`);
  }
  return pts.join("") + "Z";
}

const PATHS = [
  ...Array.from({ length: 12 }, (_, i) => ring(300, 620, 34, i + 1, 0.6)),
  ...Array.from({ length: 10 }, (_, i) => ring(1000, 180, 38, i + 1, 2.1)),
];

export function TopoBackground() {
  return (
    <svg className="pointer-events-none fixed inset-0 h-full w-full" viewBox="0 0 1200 800" preserveAspectRatio="xMidYMid slice"
         aria-hidden="true" style={{ opacity: 0.55 }}>
      {PATHS.map((d, i) => <path key={i} d={d} fill="none" stroke="var(--line)" strokeWidth="1" />)}
    </svg>
  );
}
