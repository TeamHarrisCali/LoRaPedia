import { useEffect, useId, useMemo, useRef, useState } from "react";

export interface PickNode { id: string; label: string; detail?: string }

const NODE_ID = /^![0-9a-f]{8}$/;
const SHOWN = 40;

/** Choose a node by typing part of its name or id, instead of scrolling a very long drop-down. Keyboard: arrows, Enter, Escape.
 *  With `allowId`, an exact id such as !a1b2c3d4 can be used even if the node is not in the list. */
export function NodePicker({ id, nodes, value, onChange, label, allowId = false, placeholder = "Search by name or id…", disabled = false }: {
  id: string; nodes: PickNode[]; value: string; onChange: (id: string) => void; label?: string; allowId?: boolean; placeholder?: string; disabled?: boolean;
}) {
  const listId = useId();
  const box = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const selected = nodes.find((n) => n.id === value);
  const text = selected ? selected.label : value;

  const matches = useMemo(() => {
    const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return nodes.filter((n) => words.every((w) => `${n.label} ${n.id} ${n.detail ?? ""}`.toLowerCase().includes(w)));
  }, [nodes, query]);
  const typedId = allowId && NODE_ID.test(query.trim().toLowerCase()) && !nodes.some((n) => n.id === query.trim().toLowerCase()) ? query.trim().toLowerCase() : null;
  const options: PickNode[] = [...(typedId ? [{ id: typedId, label: `Use ${typedId}` }] : []), ...matches.slice(0, SHOWN)];

  useEffect(() => { setActive(0); }, [query]);

  function pick(n: PickNode) { onChange(n.id); setOpen(false); setQuery(""); }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive((a) => Math.min(a + 1, options.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter" && open && options[active]) { e.preventDefault(); pick(options[active]); }
    else if (e.key === "Escape") { setOpen(false); setQuery(""); }
  }

  return (
    <div className="relative">
      <input ref={box} id={id} className="field pr-16" role="combobox" aria-expanded={open} aria-controls={listId} aria-autocomplete="list" aria-label={label}
        aria-activedescendant={open && options[active] ? `${listId}-${active}` : undefined} autoComplete="off" disabled={disabled}
        placeholder={placeholder} value={open ? query : text}
        onFocus={() => { setOpen(true); setQuery(""); }} onBlur={() => { setOpen(false); setQuery(""); }}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); }} onKeyDown={onKey} />
      {value && !disabled && (
        <button type="button" className="absolute right-2 top-1/2 -translate-y-1/2 rounded px-2 py-1 text-xs text-muted hover:text-ink"
          aria-label="Clear the chosen node" onMouseDown={(e) => e.preventDefault()} onClick={() => { onChange(""); box.current?.focus(); }}>Clear</button>
      )}
      {open && (
        <ul id={listId} role="listbox" aria-label={label ?? "Nodes"} className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-lg border border-line bg-surface shadow-lg">
          {options.length === 0 && <li className="px-3 py-2 text-sm text-muted" role="presentation">No node matches “{query}”.</li>}
          {options.map((n, i) => (
            <li key={n.id} id={`${listId}-${i}`} role="option" aria-selected={n.id === value}
              className={`cursor-pointer px-3 py-2 text-sm ${i === active ? "bg-amber/15" : ""} ${n.id === value ? "font-semibold" : ""}`}
              onMouseDown={(e) => e.preventDefault()} onClick={() => pick(n)} onMouseEnter={() => setActive(i)}>
              <span>{n.label}</span>{n.detail && <span className="ml-2 text-xs text-muted">{n.detail}</span>}
            </li>
          ))}
          {matches.length > SHOWN && <li className="px-3 py-2 text-xs text-muted" role="presentation">{matches.length - SHOWN} more: keep typing to narrow it down.</li>}
        </ul>
      )}
    </div>
  );
}
