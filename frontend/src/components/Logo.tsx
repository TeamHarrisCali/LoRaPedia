/** Antenna-over-open-book mark: knowledge, broadcast over the mesh. */
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <g stroke="var(--amber)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M5 21c4-2 8-2 11 0 3-2 7-2 11 0v5c-4-2-8-2-11 0-3-2-7-2-11 0z" />
        <path d="M16 21V9" />
        <path d="M11.5 12.5a6.5 6.5 0 0 1 0-6M20.5 12.5a6.5 6.5 0 0 0 0-6" />
      </g>
      <circle cx="16" cy="8.5" r="1.6" fill="var(--teal)" />
    </svg>
  );
}
