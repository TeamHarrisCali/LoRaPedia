import { useEffect, useRef } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import type { AppUpdateStatus, WizardState } from "../lib/types";
import { usePoll } from "../lib/usePoll";
import { ErrorBoundary } from "./ErrorBoundary";
import { Icon } from "./icons";
import { StatusBar } from "./StatusBar";
import { TopoBackground } from "./TopoBackground";
import { Badge } from "./ui";

const NAV = [
  { to: "/", label: "Dashboard", icon: "dashboard", end: true },
  { to: "/feed", label: "Live Feed", icon: "feed" },
  { to: "/knowledge", label: "Knowledge", icon: "book" },
  { to: "/model", label: "Model", icon: "cpu" },
  { to: "/radio", label: "Radio", icon: "radio", end: true },
  { to: "/radio/settings", label: "Radio settings", icon: "settings" },
  { to: "/messages", label: "Messages", icon: "send" },
  { to: "/limits", label: "Limits", icon: "stop" },
  { to: "/nodes", label: "Nodes", icon: "nodes" },
  { to: "/map", label: "Map", icon: "map" },
  { to: "/traceroute", label: "Traceroute", icon: "route" },
  { to: "/links", label: "Link quality", icon: "radio" },
  { to: "/telemetry", label: "Telemetry", icon: "chart" },
  { to: "/trends", label: "Trends", icon: "feed" },
  { to: "/settings", label: "Settings", icon: "settings" },
];

export function Layout() {
  const wizard = usePoll(() => api.get<WizardState>("/api/wizard"), 15000);
  const upd = usePoll(() => api.get<AppUpdateStatus>("/api/updates/status"), 60000);
  const nav = useNavigate();
  const loc = useLocation();
  const redirected = useRef(false);

  // First run: send a brand-new user to the wizard once. After that it is just a nav item.
  useEffect(() => {
    const w = wizard.data;
    if (w && !redirected.current) {
      redirected.current = true;
      if (!w.finished && w.done.length === 0 && w.skipped.length === 0 && loc.pathname === "/") nav("/setup", { replace: true });
    }
  }, [wizard.data, loc.pathname, nav]);

  const link = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-3 rounded-lg px-3 py-2 text-sm whitespace-nowrap ${isActive ? "bg-raised text-amber-text font-semibold" : "text-muted hover:bg-raised hover:text-ink"}`;

  return (
    <div className="relative min-h-screen">
      <TopoBackground />
      <div className="relative">
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-amber focus:px-3 focus:py-1 focus:text-amber-on">Skip to content</a>
        <StatusBar />
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-4 md:flex-row md:gap-6">
          <nav aria-label="Main" className="flex shrink-0 gap-1 overflow-x-auto md:w-48 md:flex-col md:overflow-visible">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.end} className={link}><Icon name={n.icon} size={17} />{n.label}{n.to === "/settings" && upd.data?.update_available && <Badge tone="amber">update</Badge>}</NavLink>
            ))}
            <NavLink to="/setup" className={link}>
              <Icon name="wand" size={17} />Setup
              {wizard.data && !wizard.data.finished && <Badge tone="amber">{wizard.data.done.length}/6</Badge>}
            </NavLink>
          </nav>
          <main id="main" className="min-w-0 flex-1 pb-16"><ErrorBoundary resetKey={loc.pathname}><Outlet /></ErrorBoundary></main>
        </div>
      </div>
    </div>
  );
}
