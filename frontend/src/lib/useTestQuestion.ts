import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorText } from "./api";
import { useLive } from "./live";
import { useToast } from "./toast";
import type { QueryRow } from "./types";

const OUTCOMES: Record<string, string> = {
  ignored: "The node ignored that message. Check the allow and block lists on the Limits page.",
  rate_limited: "That fake node asked too recently. Wait a moment and try again.",
  queue_full: "The queue is full. Try again shortly.",
  command: "That was a command (?help, ?more, ?stats). Its reply goes to the simulator outbox, not the feed.",
};

/** Send a question as a fake mesh node and wait for the answer to arrive on the live feed. Nothing goes over the air. */
export function useTestQuestion(onAnswer?: () => void) {
  const toast = useToast();
  const live = useLive();
  const [sending, setSending] = useState(false);
  const [answer, setAnswer] = useState<QueryRow | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const pending = useRef<{ sender: string; text: string } | null>(null);
  const cb = useRef(onAnswer);
  cb.current = onAnswer;

  useEffect(() => {
    const p = pending.current;
    const m = p && live.queries.find((q) => q.sender_id === p.sender && q.question === p.text);
    if (m) { pending.current = null; setSending(false); setAnswer(m); cb.current?.(); }
  }, [live.queries]);

  const ask = useCallback(async (text: string) => {
    const q = text.trim();
    if (!q) return;
    // A fresh fake node each time so the per-node rate limit never blocks a quick test.
    const sender = `!sim${Math.random().toString(16).slice(2, 7)}`;
    setSending(true); setAnswer(null); setNote(null);
    pending.current = { sender, text: q };
    const giveUp = setTimeout(() => {
      if (pending.current?.sender === sender) { pending.current = null; setSending(false); setNote("No answer yet. The model may still be loading, or it may not be running: check the Model page."); }
    }, 120_000);
    try {
      const { outcome } = await api.post<{ outcome: string }>("/api/simulator/send", { text: q, sender_id: sender, sender_name: "SIM" });
      // "delayed" = held for its node's cooldown; the answer still arrives, so keep waiting.
      if (outcome !== "queued" && outcome !== "delayed") { clearTimeout(giveUp); pending.current = null; setSending(false); setNote(OUTCOMES[outcome] ?? outcome); }
    } catch (e) { clearTimeout(giveUp); pending.current = null; setSending(false); toast.error(errorText(e)); }
  }, [toast]);

  return { ask, sending, answer, note };
}
