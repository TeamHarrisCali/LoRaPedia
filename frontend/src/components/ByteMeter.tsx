/** Thin amber bar showing how much of the byte budget a message uses. Reused wherever text is previewed. */
export function ByteMeter({ bytes, limit, className = "", showLabel = true }: { bytes: number; limit: number; className?: string; showLabel?: boolean }) {
  const over = bytes > limit;
  const pct = Math.min(100, limit > 0 ? (bytes / limit) * 100 : 0);
  return (
    <div className={`flex items-center gap-3 ${className}`}>
      <div role="meter" aria-label="Message size in bytes" aria-valuemin={0} aria-valuemax={limit} aria-valuenow={bytes}
           className="h-1.5 min-w-16 flex-1 overflow-hidden rounded-full bg-line">
        <div className={`h-full rounded-full ${over ? "bg-danger" : "bg-amber"}`} style={{ width: `${pct}%` }} />
      </div>
      {showLabel && (
        <span className={`whitespace-nowrap font-mono text-xs ${over ? "text-danger" : "text-muted"}`}>
          {bytes}/{limit} B
        </span>
      )}
    </div>
  );
}
