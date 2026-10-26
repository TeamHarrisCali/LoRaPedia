export type ThemePref = "dark" | "light" | "system";

export const getThemePref = (): ThemePref => {
  try { return (localStorage.getItem("lorapedia-theme") as ThemePref) || "dark"; } catch { return "dark"; }
};

export function applyTheme(pref: ThemePref) {
  try { localStorage.setItem("lorapedia-theme", pref); } catch { /* private mode: just don't persist */ }
  const resolved = pref === "system" ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : pref;
  document.documentElement.dataset.theme = resolved;
}
