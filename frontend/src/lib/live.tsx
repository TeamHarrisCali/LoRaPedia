import { createContext, ReactNode, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { LiveEvent, QueryRow, RadioStatus } from "./types";

interface Live {
  connected: boolean;
  /** Increments on every received packet; used to retrigger the radio dot pulse. */
  rxTick: number;
  /** Queries answered since the page loaded, newest first. */
  queries: QueryRow[];
  radio: RadioStatus | null;
  lastError: string | null;
}

const Ctx = createContext<Live>({ connected: false, rxTick: 0, queries: [], radio: null, lastError: null });
export const useLive = () => useContext(Ctx);

export function LiveProvider({ children }: { children: ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [rxTick, setRxTick] = useState(0);
  const [queries, setQueries] = useState<QueryRow[]>([]);
  const [radio, setRadio] = useState<RadioStatus | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const retry = useRef(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout>;
    let closed = false;

    const open = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onopen = () => { retry.current = 0; setConnected(true); };
      ws.onmessage = (m) => {
        const ev = JSON.parse(m.data) as LiveEvent;
        if (ev.type === "query") setQueries((q) => [{ ...ev, live: true }, ...q].slice(0, 200));
        else if (ev.type === "rx") setRxTick((n) => n + 1);
        else if (ev.type === "radio") setRadio(ev.status);
        else if (ev.type === "error") setLastError(ev.detail);
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) timer = setTimeout(open, Math.min(1000 * 2 ** retry.current++, 15000));
      };
    };
    open();
    return () => { closed = true; clearTimeout(timer); ws?.close(); };
  }, []);

  const value = useMemo(() => ({ connected, rxTick, queries, radio, lastError }), [connected, rxTick, queries, radio, lastError]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
