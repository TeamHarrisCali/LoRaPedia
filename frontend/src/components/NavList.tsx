import { NavLink } from "react-router-dom";
import { NAV_FOOT, NAV_GROUPS, type NavItem } from "../lib/nav";
import { Icon } from "./icons";
import { Badge } from "./ui";

/** The grouped list of pages: the sidebar on a computer, the same list in the drawer on a phone. */
export function NavList({ updateReady, setupDone, setupFinished, onNavigate }: {
  updateReady: boolean; setupDone: number | null; setupFinished: boolean; onNavigate?: () => void;
}) {
  const link = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-3 rounded-lg px-3 py-1.5 text-sm whitespace-nowrap ${isActive ? "bg-raised text-amber-text font-semibold" : "text-muted hover:bg-raised hover:text-ink"}`;
  const item = (n: NavItem) => (
    <li key={n.to}>
      <NavLink to={n.to} end={n.end} className={link} onClick={onNavigate} title={n.hint}>
        <Icon name={n.icon} size={17} />{n.label}
        {n.to === "/settings" && updateReady && <Badge tone="amber">update</Badge>}
        {n.to === "/setup" && !setupFinished && setupDone !== null && <Badge tone="amber">{setupDone}/6</Badge>}
      </NavLink>
    </li>
  );
  return (
    <nav aria-label="Main">
      {NAV_GROUPS.map((g) => (
        <div key={g.id} className="mb-3" role="group" aria-labelledby={g.title ? `nav-${g.id}` : undefined} aria-label={g.title ? undefined : "Overview"}>
          {g.title && <p id={`nav-${g.id}`} className="px-3 pb-1 pt-1 text-[11px] font-semibold uppercase tracking-wider text-muted">{g.title}</p>}
          <ul className="space-y-0.5">{g.items.map(item)}</ul>
        </div>
      ))}
      <ul className="mt-1 space-y-0.5 border-t border-line pt-3">{NAV_FOOT.map(item)}</ul>
    </nav>
  );
}
