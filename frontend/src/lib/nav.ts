/** Every page, grouped the way people think about them. The sidebar, the phone menu, the page headings and the "Go to" search all read this one list. */
export interface NavItem { to: string; label: string; icon: string; hint: string; keywords?: string; end?: boolean }
export interface NavGroup { id: string; title?: string; items: NavItem[] }

export const NAV_GROUPS: NavGroup[] = [
  { id: "overview", items: [
    { to: "/", label: "Dashboard", icon: "dashboard", end: true, hint: "How the node is doing, and a place to try a question", keywords: "home status overview test ask" },
    { to: "/feed", label: "Live Feed", icon: "feed", hint: "Questions and answers as they happen", keywords: "questions answers log history delivery messages queries" },
  ] },
  { id: "mesh", title: "Mesh", items: [
    { to: "/nodes", label: "Nodes", icon: "nodes", hint: "Every node the radio has heard", keywords: "devices neighbours battery hops name note label" },
    { to: "/map", label: "Map", icon: "map", hint: "Where the nodes are", keywords: "position location gps street openstreetmap tiles" },
    { to: "/messages", label: "Messages", icon: "send", hint: "Write to a node yourself", keywords: "dm direct chat send text public post" },
    { to: "/traceroute", label: "Traceroute", icon: "route", hint: "Which way a packet goes to a node", keywords: "path hops route trace" },
  ] },
  { id: "insights", title: "Insights", items: [
    { to: "/links", label: "Link quality", icon: "link", hint: "How well each neighbour is heard", keywords: "signal snr rssi range distance direct" },
    { to: "/telemetry", label: "Telemetry", icon: "chart", hint: "Battery, temperature and other sensor readings", keywords: "sensors battery voltage temperature humidity pressure csv export" },
    { to: "/trends", label: "Trends", icon: "trend", hint: "When the mesh is busiest", keywords: "busy hours packets heat map relays" },
  ] },
  { id: "answers", title: "Answers", items: [
    { to: "/knowledge", label: "Knowledge", icon: "book", hint: "Wikipedia and other libraries LoRaPedia answers from", keywords: "wikipedia zim index library articles download" },
    { to: "/model", label: "Model", icon: "cpu", hint: "The language model that writes answers", keywords: "llm ollama ai install gpu" },
    { to: "/limits", label: "Limits", icon: "shield", hint: "Who may ask, and how much", keywords: "block allow daily limit rate allowlist blocklist" },
  ] },
  { id: "radio", title: "Radio", items: [
    { to: "/radio", label: "Connection", icon: "radio", end: true, hint: "Connect the Meshtastic radio", keywords: "usb serial wifi bluetooth device port connect" },
    { to: "/radio/settings", label: "Radio settings", icon: "sliders", hint: "Region, role, name and position stored on the radio", keywords: "region lora preset hop name fixed position channel config" },
  ] },
];

/** Settings and the setup guide sit apart at the bottom: they are about LoRaPedia itself, not about the mesh. */
export const NAV_FOOT: NavItem[] = [
  { to: "/settings", label: "Settings", icon: "settings", hint: "Everything adjustable, in tabs", keywords: "options preferences units temperature theme backup update channel retention street map" },
  { to: "/setup", label: "Setup guide", icon: "wand", hint: "The first-run steps, any time", keywords: "wizard first run install getting started" },
];

export const ALL_NAV: (NavItem & { group?: string })[] = [
  ...NAV_GROUPS.flatMap((g) => g.items.map((i) => ({ ...i, group: g.title }))),
  ...NAV_FOOT,
];

/** The page a path belongs to (the longest matching link wins, so /radio/settings is not "Connection"). */
export function pageOf(path: string): (NavItem & { group?: string }) | undefined {
  const hit = ALL_NAV.filter((n) => (n.to === "/" ? path === "/" : path === n.to || path.startsWith(n.to + "/"))).sort((a, b) => b.to.length - a.to.length);
  return hit[0];
}

/** Pages whose label, hint or keywords contain every word typed. */
export function findPages(query: string) {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return ALL_NAV;
  return ALL_NAV.filter((n) => { const hay = `${n.label} ${n.hint} ${n.keywords ?? ""} ${n.group ?? ""}`.toLowerCase(); return words.every((w) => hay.includes(w)); });
}
