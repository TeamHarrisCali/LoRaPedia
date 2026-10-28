import { useCallback, useEffect, useRef, useState } from "react";

/** Poll `fn` every `ms` while the tab is visible. Returns the latest data, error, and a manual reload. */
export function usePoll<T>(fn: () => Promise<T>, ms: number, enabled = true, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const reload = useCallback(async () => {
    try {
      setData(await fnRef.current());
      setError(null);
    } catch (e) {
      setError(e as Error);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const tick = () => { if (alive && !document.hidden) void reload(); };
    tick();
    const id = setInterval(tick, ms);
    document.addEventListener("visibilitychange", tick);
    return () => { alive = false; clearInterval(id); document.removeEventListener("visibilitychange", tick); };
  }, [ms, enabled, reload, ...deps]);  // `deps`: ask again at once when what is being asked for changes

  return { data, error, reload };
}
