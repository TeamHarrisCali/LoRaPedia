import { ReactNode, useRef } from "react";

export interface TabDef { id: string; label: string; badge?: ReactNode }

/** An accessible tab strip (arrow keys, Home and End move between tabs). The page shows the panel for the active tab with <TabPanel>. */
export function Tabs({ tabs, active, onChange, label }: { tabs: TabDef[]; active: string; onChange: (id: string) => void; label: string }) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  function move(to: number) { const t = tabs[(to + tabs.length) % tabs.length]; onChange(t.id); refs.current[t.id]?.focus(); }
  function onKey(e: React.KeyboardEvent, i: number) {
    if (e.key === "ArrowRight") { e.preventDefault(); move(i + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); move(i - 1); }
    else if (e.key === "Home") { e.preventDefault(); move(0); }
    else if (e.key === "End") { e.preventDefault(); move(tabs.length - 1); }
  }
  return (
    <div role="tablist" aria-label={label} className="mb-5 flex gap-1 overflow-x-auto border-b border-line">
      {tabs.map((t, i) => (
        <button key={t.id} ref={(el) => { refs.current[t.id] = el; }} role="tab" id={`tab-${t.id}`} aria-selected={t.id === active} aria-controls={`panel-${t.id}`}
          tabIndex={t.id === active ? 0 : -1} onClick={() => onChange(t.id)} onKeyDown={(e) => onKey(e, i)}
          className={`-mb-px flex shrink-0 items-center gap-2 border-b-2 px-4 py-2.5 text-sm font-medium transition ${t.id === active ? "border-amber text-ink" : "border-transparent text-muted hover:text-ink"}`}>
          {t.label}{t.badge}
        </button>
      ))}
    </div>
  );
}

export function TabPanel({ id, active, children }: { id: string; active: string; children: ReactNode }) {
  if (id !== active) return null;
  return <div role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`}>{children}</div>;
}

/** The chosen tab, remembered for this browser tab so a reload comes back to where you were. */
export function rememberedTab(key: string, fallback: string, valid: string[]): string {
  try { const v = sessionStorage.getItem(`tab:${key}`); return v && valid.includes(v) ? v : fallback; } catch { return fallback; }
}
export function rememberTab(key: string, id: string): void {
  try { sessionStorage.setItem(`tab:${key}`, id); } catch { /* private window: the tab simply is not remembered */ }
}
