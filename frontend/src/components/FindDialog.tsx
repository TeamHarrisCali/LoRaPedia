import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { findPages } from "../lib/nav";
import type { MeshNodes } from "../lib/types";
import { Icon } from "./icons";

interface Hit { key: string; to: string; label: string; detail: string; icon: string }

/** "Go to…": type a few letters of a page (or a node's name) and press Enter. Opened with Ctrl+K (or ⌘K), or with / when no box has focus. */
export function FindDialog({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const listId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [nodes, setNodes] = useState<MeshNodes["nodes"]>([]);

  useEffect(() => { input.current?.focus(); }, []);
  useEffect(() => {
    let alive = true;
    api.get<MeshNodes>("/api/mesh/nodes").then((d) => { if (alive) setNodes(d.nodes); }).catch(() => { /* the pages still work without the node list */ });
    return () => { alive = false; };
  }, []);

  const hits = useMemo<Hit[]>(() => {
    const pages = findPages(query).map((p) => ({ key: p.to, to: p.to, label: p.label, detail: p.group ? `${p.group} · ${p.hint}` : p.hint, icon: p.icon }));
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    const found = words.length === 0 ? [] : nodes.filter((n) => words.every((w) => `${n.display} ${n.id} ${n.name ?? ""} ${n.short ?? ""}`.toLowerCase().includes(w)))
      .slice(0, 6).map((n) => ({ key: n.id, to: `/nodes/${encodeURIComponent(n.id)}`, label: n.display, detail: `Node · ${n.id}`, icon: "nodes" }));
    return [...pages, ...found];
  }, [query, nodes]);

  useEffect(() => { setActive(0); }, [query]);
  function go(h: Hit | undefined) { if (h) { onClose(); nav(h.to); } }
  function onKey(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, hits.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); go(hits[active]); }
    else if (e.key === "Escape") { e.preventDefault(); onClose(); }
    else if (e.key === "Tab") e.preventDefault();      // the box and the list are one control: keep focus in the box
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 px-4 pt-[12vh]" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-label="Go to a page or node" className="w-full max-w-lg overflow-hidden rounded-xl border border-line bg-surface shadow-2xl">
        <div className="flex items-center gap-2 border-b border-line px-3">
          <Icon name="search" size={16} className="shrink-0 text-muted" />
          <input ref={input} role="combobox" aria-expanded="true" aria-controls={listId} aria-activedescendant={hits[active] ? `${listId}-${active}` : undefined} aria-autocomplete="list"
            className="w-full bg-transparent py-3 outline-none placeholder:text-muted" placeholder="Go to a page or node…" value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={onKey} />
          <kbd className="rounded border border-line px-1.5 text-xs text-muted">Esc</kbd>
        </div>
        <ul id={listId} role="listbox" aria-label="Results" className="max-h-80 overflow-y-auto p-1.5">
          {hits.length === 0 && <li className="px-3 py-6 text-center text-sm text-muted">Nothing matches “{query}”.</li>}
          {hits.map((h, i) => (
            <li key={h.key} id={`${listId}-${i}`} role="option" aria-selected={i === active} onMouseMove={() => setActive(i)} onClick={() => go(h)}
              className={`flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2 ${i === active ? "bg-raised" : ""}`}>
              <Icon name={h.icon} size={17} className={i === active ? "text-amber-text" : "text-muted"} />
              <span className="min-w-0"><span className="block truncate text-sm font-medium">{h.label}</span><span className="block truncate text-xs text-muted">{h.detail}</span></span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
