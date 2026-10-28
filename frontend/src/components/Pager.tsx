/** "Showing 26–50 of 143" with Previous / Next and numbered pages. Renders nothing for a list that fits on one page. */
export function Pager({ page, pages, total, size, onPage, noun = "items" }: { page: number; pages: number; total: number; size: number; onPage: (p: number) => void; noun?: string }) {
  if (pages <= 1) return null;
  const from = page * size + 1, to = Math.min(total, page * size + size);
  const numbers: (number | "gap")[] = [];
  for (let p = 0; p < pages; p++) if (p === 0 || p === pages - 1 || Math.abs(p - page) <= 1) numbers.push(p); else if (numbers[numbers.length - 1] !== "gap") numbers.push("gap");
  const btn = "min-w-9 rounded-lg border px-2.5 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-40";
  return (
    <nav aria-label={`Pages of ${noun}`} className="mt-4 flex flex-wrap items-center justify-between gap-3">
      <p className="text-sm text-muted" role="status">Showing {from}–{to} of {total} {noun}</p>
      <div className="flex flex-wrap items-center gap-1">
        <button type="button" className={`${btn} border-line hover:border-muted`} disabled={page === 0} onClick={() => onPage(page - 1)}>Previous</button>
        {numbers.map((n, i) => n === "gap"
          ? <span key={`g${i}`} className="px-1 text-muted" aria-hidden="true">…</span>
          : <button key={n} type="button" aria-label={`Page ${n + 1}`} aria-current={n === page ? "page" : undefined}
              className={`${btn} ${n === page ? "border-amber/60 bg-amber/15 font-semibold" : "border-line hover:border-muted"}`} onClick={() => onPage(n)}>{n + 1}</button>)}
        <button type="button" className={`${btn} border-line hover:border-muted`} disabled={page >= pages - 1} onClick={() => onPage(page + 1)}>Next</button>
      </div>
    </nav>
  );
}
