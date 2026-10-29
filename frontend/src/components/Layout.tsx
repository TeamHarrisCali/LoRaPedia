import { useEffect, useRef, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { pageOf } from "../lib/nav";
import type { AppUpdateStatus, WizardState } from "../lib/types";
import { usePoll } from "../lib/usePoll";
import { ErrorBoundary } from "./ErrorBoundary";
import { FindDialog } from "./FindDialog";
import { Icon } from "./icons";
import { NavList } from "./NavList";
import { StatusBar } from "./StatusBar";
import { TopoBackground } from "./TopoBackground";

const typing = (el: EventTarget | null) => el instanceof HTMLElement && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));

export function Layout() {
  const wizard = usePoll(() => api.get<WizardState>("/api/wizard"), 15000);
  const upd = usePoll(() => api.get<AppUpdateStatus>("/api/updates/status"), 60000);
  const nav = useNavigate();
  const loc = useLocation();
  const redirected = useRef(false);
  const [menu, setMenu] = useState(false);     // the phone drawer
  const [finder, setFinder] = useState(false); // "Go to…"
  const menuButton = useRef<HTMLButtonElement>(null);
  const page = pageOf(loc.pathname);

  // First run: send a brand-new user to the wizard once. After that it is just a nav item.
  useEffect(() => {
    const w = wizard.data;
    if (w && !redirected.current) {
      redirected.current = true;
      if (!w.finished && w.done.length === 0 && w.skipped.length === 0 && loc.pathname === "/") nav("/setup", { replace: true });
    }
  }, [wizard.data, loc.pathname, nav]);

  useEffect(() => { document.title = page && page.to !== "/" ? `${page.label} · LoRaPedia` : "LoRaPedia"; }, [page]);
  useEffect(() => { setMenu(false); }, [loc.pathname]);

  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setFinder(true); }
      else if (e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey && !typing(e.target)) { e.preventDefault(); setFinder(true); }
      else if (e.key === "Escape") setMenu(false);
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);

  const list = <NavList updateReady={!!upd.data?.update_available} setupDone={wizard.data ? wizard.data.done.length : null} setupFinished={!!wizard.data?.finished} onNavigate={() => setMenu(false)} />;

  return (
    <div className="relative min-h-screen">
      <TopoBackground />
      <div className="relative">
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-amber focus:px-3 focus:py-1 focus:text-amber-on">Skip to content</a>
        <StatusBar menuButton={menuButton} onMenu={() => setMenu(true)} onFind={() => setFinder(true)} />
        <div className="mx-auto flex max-w-7xl gap-6 px-4 py-4">
          <aside className="hidden w-52 shrink-0 md:block">
            <div className="sticky top-16 max-h-[calc(100vh-5rem)] overflow-y-auto pb-4 pr-1">
              <button type="button" onClick={() => setFinder(true)} className="mb-4 flex w-full items-center gap-2 rounded-lg border border-line bg-surface/80 px-3 py-1.5 text-sm text-muted hover:border-muted hover:text-ink">
                <Icon name="search" size={15} />Go to…<kbd className="ml-auto rounded border border-line px-1.5 text-xs">Ctrl K</kbd>
              </button>
              {list}
            </div>
          </aside>
          <main id="main" className="min-w-0 flex-1 pb-16">
            {page?.group && <p className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">{page.group}</p>}
            <ErrorBoundary resetKey={loc.pathname}><Outlet /></ErrorBoundary>
          </main>
        </div>
      </div>
      {menu && <Drawer onClose={() => { setMenu(false); menuButton.current?.focus(); }}>{list}</Drawer>}
      {finder && <FindDialog onClose={() => setFinder(false)} />}
    </div>
  );
}

/** The phone menu: the same grouped list, sliding in over the page. */
function Drawer({ onClose, children }: { onClose: () => void; children: React.ReactNode }) {
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => { close.current?.focus(); }, []);
  return (
    <div className="fixed inset-0 z-40 md:hidden" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="absolute inset-0 bg-black/50" aria-hidden="true" />
      <div role="dialog" aria-modal="true" aria-label="Menu" className="absolute inset-y-0 left-0 w-72 max-w-[85vw] overflow-y-auto border-r border-line bg-bg p-4">
        <div className="mb-3 flex items-center justify-between">
          <span className="font-serif text-lg font-semibold">Menu</span>
          <button ref={close} type="button" onClick={onClose} aria-label="Close menu" className="rounded-lg p-2 text-muted hover:bg-raised hover:text-ink"><Icon name="x" size={18} /></button>
        </div>
        {children}
      </div>
    </div>
  );
}
