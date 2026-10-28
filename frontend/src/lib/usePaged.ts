import { useEffect, useState } from "react";

/** Split a long list into pages. The page goes back to the first when `resetKey` changes (a new search or filter), and never points past the end. */
export function usePaged<T>(items: T[], size: number, resetKey: unknown = "") {
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(items.length / size));
  useEffect(() => { setPage(0); }, [resetKey, size]);
  const current = Math.min(page, pages - 1);
  return { page: current, pages, total: items.length, size, setPage, slice: items.slice(current * size, current * size + size) };
}
