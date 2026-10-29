import type { RefObject } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { useLive } from "../lib/live";
import { indexPct } from "../lib/indexing";
import type { Health, IndexProgress } from "../lib/types";
import { usePoll } from "../lib/usePoll";
import { Icon } from "./icons";
import { Logo } from "./Logo";
import { Dot } from "./ui";

/** Persistent top bar: radio / model / knowledge status dots and queue length, plus the menu and search buttons on a phone. Visible on every page. */
export function StatusBar({ menuButton, onMenu, onFind }: { menuButton: RefObject<HTMLButtonElement>; onMenu: () => void; onFind: () => void }) {
  const health = usePoll(() => api.get<Health>("/api/health"), 3000);
  const llm = usePoll(() => api.get<{ ok: boolean; detail: string }>("/api/llm/status"), 10000);
  const { rxTick, connected } = useLive();
  const h = health.data;
  const indexing = h?.indexing === "running" || h?.indexing === "paused";
  const ix = usePoll(() => api.get<IndexProgress>("/api/kb/index/status"), 2000, indexing);   // only asked while a job is going
  const pct = indexing ? indexPct(ix.data) : null;

  const radio = !h
    ? { tone: "danger" as const, text: "Server unreachable" }
    : h.radio === "meshtastic" ? { tone: "teal" as const, text: "Radio connected" } : { tone: "amber" as const, text: "Radio: simulator (no device)" };
  const llmDot = !llm.data
    ? { tone: "muted" as const, text: "Model: checking" }
    : llm.data.ok ? { tone: "teal" as const, text: `Model ready (${llm.data.detail})` } : { tone: "danger" as const, text: `Model not ready: ${llm.data.detail}` };
  const index = !h ? { tone: "muted" as const, text: "Knowledge base: unknown" }
    : h.indexing === "running" ? { tone: "amber" as const, text: `Knowledge base: indexing${pct !== null ? `, ${pct}% done${ix.data?.phase_label ? ` (${ix.data.phase_label})` : ""}` : ""}` }
    : h.indexing === "paused" ? { tone: "amber" as const, text: `Knowledge base: indexing paused${pct !== null ? ` at ${pct}%` : ""}` }
    : h.knowledge_base === "index" ? { tone: "teal" as const, text: "Knowledge base ready" } : { tone: "amber" as const, text: "Knowledge base: demo data only" };

  const item = "flex items-center gap-1.5 rounded-md px-1 py-1 text-sm hover:bg-raised sm:gap-2 sm:px-2";
  return (
    <header className="sticky top-0 z-30 flex flex-wrap items-center gap-x-2 gap-y-1 sm:gap-x-4 border-b border-line bg-bg/95 px-4 py-2 backdrop-blur">
      <button ref={menuButton} type="button" onClick={onMenu} aria-label="Open menu" className="-ml-2 rounded-lg p-2 text-muted hover:bg-raised hover:text-ink md:hidden"><Icon name="menu" size={20} /></button>
      <Link to="/" className="flex items-center gap-2 sm:pr-2" aria-label="LoRaPedia home">
        <Logo />
        <span className="font-serif text-lg font-semibold">LoRaPedia</span>
      </Link>
      <nav aria-label="Status" className="flex flex-wrap items-center gap-1">
        <Link to="/radio" className={item}>
          <span key={rxTick} className={`inline-flex rounded-full ${rxTick ? "pulse" : ""}`}><Dot tone={radio.tone} label={radio.text} /></span>
          <span className="hidden sm:inline">Radio</span>
        </Link>
        <Link to="/model" className={item}><Dot tone={llmDot.tone} label={llmDot.text} /><span className="hidden sm:inline">Model</span></Link>
        <Link to="/knowledge" className={item}><Dot tone={index.tone} label={index.text} /><span className="hidden sm:inline">Knowledge</span>{pct !== null && <span className="font-mono text-sm text-amber-text">{h?.indexing === "paused" ? "paused " : ""}{pct}%</span>}</Link>
      </nav>
      <span className="ml-auto flex items-center gap-3 text-sm text-muted">
        <button type="button" onClick={onFind} aria-label="Go to a page or node" className="rounded-lg p-2 hover:bg-raised hover:text-ink md:hidden"><Icon name="search" size={18} /></button>
        {!connected && <span className="text-danger" role="status">Live feed disconnected</span>}
        <span><span className="hidden sm:inline">Queue: </span><span className="sr-only sm:hidden">Queue: </span><span className="font-mono text-ink">{h?.queue ?? "–"}</span></span>
      </span>
    </header>
  );
}
