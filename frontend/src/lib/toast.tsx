import { createContext, ReactNode, useCallback, useContext, useMemo, useRef, useState } from "react";

interface ToastAction { label: string; run: () => void }
interface Toast { id: number; kind: "success" | "error" | "info"; text: string; actions?: ToastAction[] }

interface ToastApi {
  success: (text: string) => void;
  info: (text: string) => void;
  /** Errors stay until dismissed or until an action is taken: they usually need a decision. */
  error: (text: string, actions?: ToastAction[]) => void;
}

const Ctx = createContext<ToastApi>({ success() {}, info() {}, error() {} });
export const useToast = () => useContext(Ctx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const push = useCallback((kind: Toast["kind"], text: string, actions?: ToastAction[]) => {
    const id = nextId.current++;
    setToasts((t) => [...t.slice(-3), { id, kind, text, actions }]);
    if (kind !== "error") setTimeout(() => dismiss(id), 5000);
  }, [dismiss]);

  const api = useMemo<ToastApi>(() => ({
    success: (t) => push("success", t),
    info: (t) => push("info", t),
    error: (t, a) => push("error", t, a),
  }), [push]);

  const tone = { success: "border-teal", info: "border-line", error: "border-danger" } as const;
  return (
    <Ctx.Provider value={api}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2" role="region" aria-label="Notifications">
        {toasts.map((t) => (
          <div key={t.id} role={t.kind === "error" ? "alert" : "status"}
               className={`toast-in rounded-lg border-l-4 ${tone[t.kind]} border border-line bg-raised p-3 shadow-lg`}>
            <div className="flex items-start gap-3">
              <p className="flex-1 text-sm">{t.text}</p>
              <button onClick={() => dismiss(t.id)} aria-label="Dismiss notification" className="text-muted hover:text-ink">✕</button>
            </div>
            {t.actions && (
              <div className="mt-2 flex gap-2">
                {t.actions.map((a) => (
                  <button key={a.label} onClick={() => { a.run(); dismiss(t.id); }}
                          className="rounded border border-line px-2 py-1 text-sm font-medium text-amber-text hover:bg-surface">
                    {a.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
