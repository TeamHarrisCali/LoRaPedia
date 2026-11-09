import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, errorText } from "../lib/api";
import { useToast } from "../lib/toast";
import { Icon } from "./icons";
import { Button } from "./ui";

const RANGES = [
  { id: "hour", label: "Last hour", query: "hours=1" },
  { id: "day", label: "Last 24 hours", query: "hours=24" },
  { id: "week", label: "Last 7 days", query: "hours=168" },
  { id: "month", label: "Last 30 days", query: "hours=720" },
  { id: "all", label: "Everything", query: "all=true" },
] as const;

/** "Clear…": delete the questions from the last hour, day, week or month, or all of them, like clearing browser history. */
export function ClearHistory({ onCleared }: { onCleared?: () => void }) {
  const [open, setOpen] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button ref={opener} type="button" onClick={() => setOpen(true)}
        className="inline-flex items-center justify-center gap-2 rounded-lg border border-line bg-raised px-3.5 py-2 text-sm transition hover:border-muted"><Icon name="trash" size={16} />Clear…</button>
      {open && <ClearDialog onClose={() => { setOpen(false); opener.current?.focus(); }} onCleared={onCleared} />}
    </>
  );
}

function ClearDialog({ onClose, onCleared }: { onClose: () => void; onCleared?: () => void }) {
  const toast = useToast();
  const titleId = useId();
  const [range, setRange] = useState<(typeof RANGES)[number]["id"]>("day");
  const [count, setCount] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const first = useRef<HTMLInputElement>(null);
  const query = RANGES.find((r) => r.id === range)!.query;

  useEffect(() => { first.current?.focus(); }, []);
  useEffect(() => {
    let alive = true;
    setCount(null);
    api.get<{ count: number }>(`/api/queries/count?${query}`).then((d) => { if (alive) setCount(d.count); }).catch((e) => toast.error(errorText(e)));
    return () => { alive = false; };
  }, [query]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const on = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [onClose]);

  async function clear() {
    setBusy(true);
    try {
      const r = await api.del<{ deleted: number }>(`/api/queries?${query}`);
      toast.success(r.deleted === 1 ? "Deleted 1 question." : `Deleted ${r.deleted.toLocaleString()} questions.`);
      onCleared?.();
      onClose();
    } catch (e) { toast.error(errorText(e)); setBusy(false); }
  }

  const noun = (n: number) => `${n.toLocaleString()} question${n === 1 ? "" : "s"}`;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 px-4 pt-[10vh]" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-labelledby={titleId} className="w-full max-w-md rounded-xl border border-line bg-surface p-5 shadow-2xl">
        <h2 id={titleId} className="text-xl font-semibold">Clear questions</h2>
        <p className="mt-1 text-sm text-muted">Delete the questions and answers logged in:</p>
        <fieldset className="mt-3">
          <legend className="sr-only">Time range</legend>
          <div className="space-y-1">
            {RANGES.map((r, i) => (
              <label key={r.id} className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2 text-sm ${range === r.id ? "border-amber bg-raised" : "border-line hover:border-muted"}`}>
                <input ref={i === 1 ? first : undefined} type="radio" name="clear-range" checked={range === r.id} onChange={() => setRange(r.id)} />{r.label}
              </label>
            ))}
          </div>
        </fieldset>
        <p className="mt-3 text-sm" role="status" aria-live="polite">
          {count === null ? <span className="text-muted">Counting…</span> : count === 0 ? <span className="text-muted">Nothing was logged in that time.</span> : <b>{noun(count)} will be deleted.</b>}
        </p>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-muted">
          <li>It cannot be undone. The text is overwritten in the log file.</li>
          <li>The node also forgets what it remembered for follow-up questions, and anyone who asked today gets today's questions back (their daily limit counts from this log).</li>
          <li>Backups taken earlier still hold these questions until they are replaced or deleted (<Link className="underline" to="/settings" onClick={onClose}>Settings</Link>).</li>
        </ul>
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="danger" busy={busy} disabled={!count} onClick={() => void clear()}>{count ? `Delete ${noun(count)}` : "Delete"}</Button>
        </div>
      </div>
    </div>
  );
}
