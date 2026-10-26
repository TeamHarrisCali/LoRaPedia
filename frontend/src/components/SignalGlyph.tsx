/** Four-bar signal glyph from SNR (dB). Bars are filled amber; the label carries the meaning for screen readers. */
export function SignalGlyph({ snr, size = 14 }: { snr: number | null | undefined; size?: number }) {
  const level = snr == null ? 0 : snr >= 5 ? 4 : snr >= 0 ? 3 : snr >= -7 ? 2 : snr >= -15 ? 1 : 0;
  const label = snr == null ? "Signal unknown" : `Signal ${level} of 4 (SNR ${snr} dB)`;
  return (
    <svg width={size} height={size} viewBox="0 0 14 14" role="img" aria-label={label}>
      <title>{label}</title>
      {[0, 1, 2, 3].map((i) => (
        <rect key={i} x={i * 3.6} y={13 - (i + 1) * 3} width={2.6} height={(i + 1) * 3} rx={0.8}
              fill={i < level ? "var(--amber)" : "var(--line)"} />
      ))}
    </svg>
  );
}
