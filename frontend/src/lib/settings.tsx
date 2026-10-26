import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, errorText } from "./api";
import { useToast } from "./toast";
import type { Settings } from "./types";

interface Ctx {
  settings: Settings | null;
  defaults: Settings | null;
  save: (next: Settings) => Promise<boolean>;
  reload: () => Promise<void>;
}
const SettingsCtx = createContext<Ctx>({ settings: null, defaults: null, save: async () => false, reload: async () => {} });
export const useSettings = () => useContext(SettingsCtx);

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [defaults, setDefaults] = useState<Settings | null>(null);
  const toast = useToast();

  const reload = useCallback(async () => {
    try { setSettings(await api.get<Settings>("/api/settings")); } catch (e) { toast.error(errorText(e)); }
  }, [toast]);

  useEffect(() => {
    void reload();
    api.get<Settings>("/api/settings/defaults").then(setDefaults).catch(() => {});
  }, [reload]);

  const save = useCallback(async (next: Settings) => {
    try { setSettings(await api.put<Settings>("/api/settings", next)); return true; }
    catch (e) { toast.error(`Could not save settings. ${errorText(e)}`); return false; }
  }, [toast]);

  return <SettingsCtx.Provider value={{ settings, defaults, save, reload }}>{children}</SettingsCtx.Provider>;
}

/**
 * Local editable copy of the settings with dirty tracking, so pages save explicitly.
 * Saving sends only the fields the user touched, layered over the latest server copy, so changes made
 * elsewhere in the meantime (e.g. blocking a node, which applies immediately) are never overwritten.
 */
export function useDraft() {
  const { settings, save } = useSettings();
  const toast = useToast();
  const [draft, setDraft] = useState<Settings | null>(settings);
  const touched = useRef(new Set<keyof Settings>());
  const [dirty, setDirty] = useState(false);

  // Keep untouched fields in sync with the server; touched ones keep the user's edit.
  useEffect(() => {
    if (!settings) return;
    setDraft((d) => {
      if (!d) return settings;
      const merged = { ...settings } as Settings;
      touched.current.forEach((k) => { (merged as unknown as Record<string, unknown>)[k] = d[k]; });
      return merged;
    });
  }, [settings]);

  const set = useCallback((patch: Partial<Settings>) => {
    (Object.keys(patch) as (keyof Settings)[]).forEach((k) => touched.current.add(k));
    setDraft((d) => (d ? { ...d, ...patch } : d));
    setDirty(true);
  }, []);

  return {
    draft, set, dirty,
    save: async () => {
      if (!draft || !settings) return false;
      const next = { ...settings } as Settings;
      touched.current.forEach((k) => { (next as unknown as Record<string, unknown>)[k] = draft[k]; });
      const ok = await save(next);
      if (ok) { touched.current.clear(); setDirty(false); toast.success("Saved."); }
      return ok;
    },
    discard: () => { touched.current.clear(); setDraft(settings); setDirty(false); },
  };
}
