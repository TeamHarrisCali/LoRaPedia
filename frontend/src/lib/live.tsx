import { createContext, ReactNode, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { DeliveryPart, LiveEvent, QueryRow, RadioStatus } from "./types";
import { worstDelivery } from "./delivery";

interface Live {
  connected: boolean;
  /** Increments on every received packet; used to retrigger the radio dot pulse. */
  rxTick: number;
  /** Queries answered since the page loaded, newest first. */
  queries: QueryRow[];
  radio: RadioStatus | null;
  lastError: string | null;
  /** Increment when the radio reports what became of a message, or a message arrives for the operator: pages that list them refetch. */
  deliveryTick: number;
  messageTick: number;
}

const Ctx = createContext<Live>({ connected: false, rxTick: 0, queries: [], radio: null, lastError: null, deliveryTick: 0, messageTick: 0 });
export const useLive = () => useContext(Ctx);

export function LiveProvider({ children }: { children: ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [rxTick, setRxTick] = useState(0);
  const [queries, setQueries] = useState<QueryRow[]>([]);
  const [radio, setRadio] = useState<RadioStatus | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [deliveryTick, setDeliveryTick] = useState(0);
  const [messageTick, setMessageTick] = useState(0);
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
        else if (ev.type === "delivery") {
          setQueries((all) => all.map((q) => {
            const parts = q.delivery?.parts;
            if (!parts?.some((p) => p.packet_id === ev.packet_id)) return q;
            const next: DeliveryPart[] = parts.map((p) => (p.packet_id === ev.packet_id ? { ...p, state: ev.state, detail: ev.detail } : p));
            return { ...q, delivery: { state: worstDelivery(next.map((p) => p.state)), parts: next } };
          }));
          setDeliveryTick((n) => n + 1);
        } else if (ev.type === "message") setMessageTick((n) => n + 1);
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

  const value = useMemo(() => ({ connected, rxTick, queries, radio, lastError, deliveryTick, messageTick }), [connected, rxTick, queries, radio, lastError, deliveryTick, messageTick]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
