import { useEffect, useState } from "react";

const reducedMotion = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Reveals `text` like a typewriter when `animate` is set (and motion is allowed); otherwise shows it at once. */
export function Typewriter({ text, animate }: { text: string; animate: boolean }) {
  const instant = !animate || reducedMotion();
  const [n, setN] = useState(instant ? text.length : 0);
  useEffect(() => {
    if (instant) { setN(text.length); return; }
    setN(0);
    const id = setInterval(() => setN((v) => { if (v >= text.length) { clearInterval(id); return v; } return v + 2; }), 18);
    return () => clearInterval(id);
  }, [text, instant]);
  const shown = text.slice(0, n);
  return <span className={n < text.length ? "caret" : ""}>{shown}</span>;
}

/** Monospace chat bubble. `side` follows the conversation: questions in, answers out. */
export function Bubble({ side, children, label }: { side: "in" | "out"; children: React.ReactNode; label: string }) {
  const tone = side === "out" ? "border-amber/60 bg-amber/10" : "border-line bg-raised";
  return (
    <div className={`flex ${side === "out" ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[92%] whitespace-pre-wrap break-words rounded-2xl border px-3.5 py-2 font-mono text-[13px] leading-relaxed ${tone}`}>
        <span className="sr-only">{label}: </span>
        {children}
      </div>
    </div>
  );
}
