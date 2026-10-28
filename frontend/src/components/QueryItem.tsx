import { useState } from "react";
import { clockTime, maxBytes } from "../lib/format";
import { partsOf } from "../lib/useQueries";
import type { QueryRow } from "../lib/types";
import { Bubble, Typewriter } from "./Bubble";
import { ByteMeter } from "./ByteMeter";
import { Icon } from "./icons";
import { Badge } from "./ui";

/** One question/answer exchange. With `expandable`, clicking reveals retrieved chunks and the full prompt. */
export function QueryItem({ q, expandable = false }: { q: QueryRow; expandable?: boolean }) {
  const [open, setOpen] = useState(false);
  const parts = partsOf(q);
  const bytes = q.bytes ?? maxBytes(parts);
  const ts = q.ts ?? Date.now() / 1000;
  const fresh = !!q.live && Date.now() / 1000 - ts < 8; // only animate what just arrived

  const body = (
    <>
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
        <span className="font-mono">{clockTime(ts)}</span>
        <span className="font-semibold text-ink">{q.sender_name}</span>
        <span className="font-mono">{q.sender_id}</span>
        {q.debug?.demo && <Badge>demo</Badge>}
        <span className="ml-auto font-mono">{q.latency_s.toFixed(1)}s</span>
        {expandable && <Icon name="chevron" size={14} className={`transition ${open ? "rotate-90" : ""}`} />}
      </div>
      <div className="space-y-1.5">
        <Bubble side="in" label="Question">{q.question}</Bubble>
        {parts.map((p, i) => <Bubble key={i} side="out" label={parts.length > 1 ? `Answer part ${i + 1}` : "Answer"}><Typewriter text={p} animate={fresh} /></Bubble>)}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <ByteMeter bytes={bytes} limit={q.byte_limit} className="min-w-48 flex-1" />
        {parts.length > 1 && <Badge tone="amber">{parts.length} parts</Badge>}
        {q.debug?.fallback && <Badge tone="amber">AI memory, unverified</Badge>}
        {q.debug?.mesh && <Badge tone="teal">from the mesh data</Badge>}
        {q.sources.map((s) => <Badge key={s} tone="teal">{s}</Badge>)}
      </div>
    </>
  );

  if (!expandable) return <li className="rounded-xl border border-line bg-surface/90 p-4">{body}</li>;
  return (
    <li className="rounded-xl border border-line bg-surface/90">
      <button className="block w-full p-4 text-left" aria-expanded={open} onClick={() => setOpen(!open)}>{body}</button>
      {open && (
        <div className="space-y-4 border-t border-line p-4 text-sm">
          <div>
            <h3 className="mb-1 text-base font-semibold">Retrieved chunks</h3>
            {q.debug.chunks.length === 0 ? <p className="text-muted">Nothing relevant was found in the library.</p> : (
              <ol className="space-y-2">
                {q.debug.chunks.map((c, i) => (
                  <li key={i} className="rounded-lg border border-line bg-bg p-3">
                    <p className="mb-1 flex justify-between"><span className="font-semibold">{i + 1}. {c.title}</span><span className="font-mono text-xs text-muted">score {c.score}</span></p>
                    <p className="text-muted">{c.text}</p>
                  </li>
                ))}
              </ol>
            )}
          </div>
          <div>
            <h3 className="mb-1 text-base font-semibold">Full prompt {q.debug.compressed && <Badge tone="amber">compressed once</Badge>}</h3>
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-bg p-3 font-mono text-xs">{q.debug.prompt}</pre>
          </div>
        </div>
      )}
    </li>
  );
}
