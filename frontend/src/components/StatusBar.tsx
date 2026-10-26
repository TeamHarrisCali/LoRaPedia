import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { useLive } from "../lib/live";
import type { Health } from "../lib/types";
import { usePoll } from "../lib/usePoll";
import { Logo } from "./Logo";
import { Dot } from "./ui";

/** Persistent top bar: radio / LLM / index status dots and queue length. Visible on every page. */
export function StatusBar() {
  const health = usePoll(() => api.get<Health>("/api/health"), 3000);
  const llm = usePoll(() => api.get<{ ok: boolean; detail: string }>("/api/llm/status"), 10000);
  const { rxTick, connected } = useLive();
  const h = health.data;

  const radio = !h
    ? { tone: "danger" as const, text: "Server unreachable" }
    : h.radio === "meshtastic" ? { tone: "teal" as const, text: "Radio connected" } : { tone: "amber" as const, text: "Radio: simulator (no device)" };
  const llmDot = !llm.data
    ? { tone: "muted" as const, text: "LLM: checking" }
    : llm.data.ok ? { tone: "teal" as const, text: `LLM ready (${llm.data.detail})` } : { tone: "danger" as const, text: `LLM not ready: ${llm.data.detail}` };
  const index = !h ? { tone: "muted" as const, text: "Knowledge base: unknown" }
    : h.indexing === "running" ? { tone: "amber" as const, text: "Knowledge base: indexing" }
    : h.knowledge_base === "index" ? { tone: "teal" as const, text: "Knowledge base ready" } : { tone: "amber" as const, text: "Knowledge base: demo data only" };

  const item = "flex items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-raised";
  return (
    <header className="sticky top-0 z-30 flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line bg-bg/95 px-4 py-2 backdrop-blur">
      <Link to="/" className="flex items-center gap-2 pr-2" aria-label="LoRaPedia home">
        <Logo />
        <span className="font-serif text-lg font-semibold">LoRaPedia</span>
      </Link>
      <nav aria-label="Status" className="flex flex-wrap items-center gap-1">
        <Link to="/radio" className={item}>
          <span key={rxTick} className={`inline-flex rounded-full ${rxTick ? "pulse" : ""}`}><Dot tone={radio.tone} label={radio.text} /></span>
          Radio
        </Link>
        <Link to="/model" className={item}><Dot tone={llmDot.tone} label={llmDot.text} />LLM</Link>
        <Link to="/knowledge" className={item}><Dot tone={index.tone} label={index.text} />Index</Link>
      </nav>
      <span className="ml-auto flex items-center gap-3 text-sm text-muted">
        {!connected && <span className="text-danger" role="status">Live feed disconnected</span>}
        <span>Queue: <span className="font-mono text-ink">{h?.queue ?? "–"}</span></span>
      </span>
    </header>
  );
}
