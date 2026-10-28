import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { NodePicker } from "../components/NodePicker";
import { Pager } from "../components/Pager";
import { TabPanel, Tabs, rememberTab, rememberedTab } from "../components/Tabs";
import { Badge, Button, Card, EmptyState, Field, PageHeader, SectionTitle } from "../components/ui";
import { api, errorText } from "../lib/api";
import { clockTime, timeAgo } from "../lib/format";
import { useToast } from "../lib/toast";
import type { RadioStatus, RcBackup, RcChange, RcField, RcSaved, RcSection, RcValue, RcView } from "../lib/types";
import { usePaged } from "../lib/usePaged";
import { usePoll } from "../lib/usePoll";

type Edits = Record<string, Record<string, RcValue>>;
const pretty = (s: string) => s.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());
const show = (v: RcValue) => (typeof v === "boolean" ? (v ? "on" : "off") : String(v));

function FieldInput({ f, value, onChange, disabled, id }: { f: RcField; value: RcValue; onChange: (v: RcValue) => void; disabled: boolean; id: string }) {
  if (f.kind === "bool") return <input id={id} type="checkbox" role="switch" className="h-5 w-5" checked={value === true} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />;
  if (f.kind === "enum") {
    const choices = f.choices ?? [];
    if (choices.length > 10) return <NodePicker id={id} label={f.label} disabled={disabled} value={String(value)} onChange={(v) => v && onChange(v)} placeholder="Search the choices…"
      nodes={choices.map((c) => ({ id: c, label: pretty(c), detail: c }))} />;
    return <select id={id} className="field" value={String(value)} disabled={disabled} onChange={(e) => onChange(e.target.value)}>{choices.map((c) => <option key={c} value={c}>{pretty(c)}</option>)}</select>;
  }
  if (f.kind === "string") return <input id={id} className="field" maxLength={f.max} value={String(value)} disabled={disabled} onChange={(e) => onChange(e.target.value)} />;
  return <input id={id} type="number" className="field max-w-xs" min={f.min} max={f.max} step={f.kind === "float" ? "any" : 1} value={String(value)} disabled={disabled}
    onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))} />;
}

function FieldRow({ section, f, edits, setEdit, disabled, tag }: { section: string; f: RcField; edits: Edits; setEdit: (s: string, n: string, v: RcValue) => void; disabled: boolean; tag?: string }) {
  const value = edits[section]?.[f.name] ?? f.value;
  const changed = edits[section]?.[f.name] !== undefined && edits[section][f.name] !== f.value;
  const id = `rc-${section}-${f.name}`;
  return (
    <div className={`mb-4 rounded-lg border p-3 ${changed ? "border-amber/60 bg-amber/10" : "border-transparent"}`}>
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <label htmlFor={id} className="text-sm font-medium">{f.label}</label>
        {tag && <Badge>{tag}</Badge>}
        {changed && <Badge tone="amber">changed from {show(f.value)}</Badge>}
      </div>
      <FieldInput f={f} value={value} onChange={(v) => setEdit(section, f.name, v)} disabled={disabled} id={id} />
      {f.help && <p className="mt-1 text-xs text-muted">{f.help}</p>}
      {f.risk && <p className="mt-1 text-xs text-amber-text">{f.risk}</p>}
    </div>
  );
}

function NamePosition({ writable, status, onDone }: { writable: boolean; status: RadioStatus | null; onDone: () => void }) {
  const toast = useToast();
  const [long, setLong] = useState<string | null>(null);
  const [short, setShort] = useState<string | null>(null);
  const [lat, setLat] = useState(""), [lon, setLon] = useState(""), [alt, setAlt] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<{ message: string }>) => {
    setBusy(true);
    try { toast.success((await fn()).message); onDone(); } catch (e) { toast.error(errorText(e)); } finally { setBusy(false); }
  };
  const latN = lat.trim() === "" ? NaN : Number(lat), lonN = lon.trim() === "" ? NaN : Number(lon);
  const okPos = Number.isFinite(latN) && Number.isFinite(lonN) && latN >= -90 && latN <= 90 && lonN >= -180 && lonN <= 180;
  return (
    <>
      <Card className="mb-5">
        <SectionTitle hint="How this radio shows up on other people's screens. It restarts to apply a new name.">Name</SectionTitle>
        <div className="grid gap-x-4 sm:grid-cols-[1fr_8rem]">
          <Field label="Long name" htmlFor="rc-long" help="Up to 39 bytes."><input id="rc-long" className="field" disabled={!writable} value={long ?? status?.device.long_name ?? ""} onChange={(e) => setLong(e.target.value)} /></Field>
          <Field label="Short name" htmlFor="rc-short" help="Up to 4 characters."><input id="rc-short" className="field" maxLength={4} disabled={!writable} value={short ?? status?.device.short_name ?? ""} onChange={(e) => setShort(e.target.value)} /></Field>
        </div>
        <Button disabled={!writable || busy} onClick={() => void run(() => api.put("/api/radio/config/names", { long_name: long ?? status?.device.long_name ?? "", short_name: short ?? status?.device.short_name ?? "" }))}>Send the name to the radio</Button>
      </Card>
      <Card className="mb-5">
        <SectionTitle hint="A radio with no GPS does not know where it is, so the Map cannot place it and distances and link quality cannot be worked out. Tell it once and it remembers.">Fixed position</SectionTitle>
        <p className="mb-3 rounded-lg border border-amber/50 bg-amber/10 p-3 text-sm">This position is <b>shared with the whole mesh</b>: everyone who hears this radio can see where it is. Use a spot you are happy to share (a nearby corner is fine).</p>
        <div className="grid gap-x-4 sm:grid-cols-3">
          <Field label="Latitude" htmlFor="rc-lat" help="−90 to 90, like 46.8523."><input id="rc-lat" inputMode="decimal" className="field" disabled={!writable} value={lat} onChange={(e) => setLat(e.target.value)} /></Field>
          <Field label="Longitude" htmlFor="rc-lon" help="−180 to 180, like −121.7603."><input id="rc-lon" inputMode="decimal" className="field" disabled={!writable} value={lon} onChange={(e) => setLon(e.target.value)} /></Field>
          <Field label="Altitude (metres)" htmlFor="rc-alt" help="Optional."><input id="rc-alt" inputMode="numeric" className="field" disabled={!writable} value={alt} onChange={(e) => setAlt(e.target.value)} /></Field>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" disabled={!writable || busy || !okPos} onClick={() => void run(() => api.put("/api/radio/config/position", { lat: latN, lon: lonN, alt: alt.trim() === "" ? 0 : Number(alt) }))}>Set this position</Button>
          <Button disabled={!writable || busy} onClick={() => void run(() => api.del("/api/radio/config/position"))}>Remove the fixed position</Button>
        </div>
      </Card>
    </>
  );
}

function Copies({ backups, writable, onDone, onPull }: { backups: RcBackup[]; writable: boolean; onDone: () => void; onPull: () => void }) {
  const toast = useToast();
  const paged = usePaged(backups, 8);
  const [ask, setAsk] = useState<number | null>(null);
  const [mismatch, setMismatch] = useState<string | null>(null);
  async function restore(id: number, force: boolean) {
    try { const r = await api.post<RcSaved>(`/api/radio/config/backups/${id}/restore`, { force }); toast.success(r.message); setAsk(null); setMismatch(null); onDone(); }
    catch (e) {
      const text = errorText(e);
      if (/different radio/.test(text)) { setMismatch(text); } else { toast.error(text); setAsk(null); }
    }
  }
  return (
    <Card className="mb-5">
      <div className="mb-2 flex flex-wrap items-end justify-between gap-2">
        <SectionTitle hint="A copy of the radio's settings is kept before every change, so you can go back. Passwords, keys and MQTT credentials are never copied.">Saved copies</SectionTitle>
        <Button onClick={onPull}>Read the radio now and keep a copy</Button>
      </div>
      {backups.length === 0 ? <p className="text-sm text-muted">None yet.</p> : (
        <ul className="divide-y divide-line" aria-label="Saved copies of the radio's settings">
          {paged.slice.map((b) => (
            <li key={b.id} className="py-2 text-sm">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="font-medium">{pretty(b.reason)}</span>
                <span className="text-muted">{timeAgo(b.ts)} · {clockTime(b.ts)}</span>
                <span className="font-mono text-xs text-muted">{b.radio ?? "radio unknown"}</span>
                <span className="ml-auto"><Button variant="ghost" disabled={!writable} onClick={() => { setAsk(b.id); setMismatch(null); }}>Restore…</Button></span>
              </div>
              {ask === b.id && (
                <div className="mt-2 rounded-lg border border-line bg-bg p-3" role="alertdialog" aria-label="Confirm restore">
                  <p>Write these settings back to the radio? The current settings are copied first, and the radio restarts.</p>
                  {mismatch && <p className="mt-2 text-amber-text">{mismatch}</p>}
                  <div className="mt-2 flex gap-2">
                    <Button variant="primary" onClick={() => void restore(b.id, !!mismatch)}>{mismatch ? "Restore anyway" : "Restore"}</Button>
                    <Button variant="ghost" onClick={() => { setAsk(null); setMismatch(null); }}>Cancel</Button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      <Pager page={paged.page} pages={paged.pages} total={paged.total} size={paged.size} onPage={paged.setPage} noun="copies" />
    </Card>
  );
}

export function RadioSettings() {
  const toast = useToast();
  const view = usePoll(() => api.get<RcView>("/api/radio/config"), 20000);
  const status = usePoll(() => api.get<RadioStatus>("/api/radio/status"), 20000);
  const v = view.data;
  const tabIds = useMemo(() => [...(v?.sections.map((s) => s.name) ?? []), "name", "copies"], [v]);
  const [tab, setTab] = useState(() => rememberedTab("radiosettings", "lora", ["lora", "device", "position", "power", "display", "bluetooth", "telemetry", "neighbor_info", "store_forward", "range_test", "external_notification", "name", "copies"]));
  const [find, setFind] = useState("");
  const [edits, setEdits] = useState<Edits>({});
  const [review, setReview] = useState(false);
  const [busy, setBusy] = useState(false);
  const writable = !!v?.writable;

  const setEdit = (s: string, n: string, val: RcValue) => setEdits((e) => ({ ...e, [s]: { ...e[s], [n]: val } }));
  const changes = useMemo(() => {
    const out: RcChange[] = [];
    for (const s of v?.sections ?? []) for (const f of s.fields) {
      const e = edits[s.name]?.[f.name];
      if (e !== undefined && e !== f.value) out.push({ section: s.name, field: f.name, label: f.label, old: f.value, new: e, risk: f.risk ?? null });
    }
    return out;
  }, [v, edits]);
  const invalid = changes.some((c) => c.new === "");

  const results = useMemo(() => {
    const words = find.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return [];
    return (v?.sections ?? []).flatMap((s) => s.fields.filter((f) => words.every((w) => `${f.label} ${f.name} ${f.help ?? ""} ${s.label}`.toLowerCase().includes(w))).map((f) => ({ s, f })));
  }, [v, find]);
  const found = usePaged(results, 12, find);

  async function save() {
    const body: Edits = {};
    for (const c of changes) (body[c.section] ??= {})[c.field] = c.new;
    setBusy(true);
    try {
      const r = await api.put<RcSaved>("/api/radio/config", { changes: body });
      toast.success(r.message); setEdits({}); setReview(false);
      setTimeout(() => void view.reload(), 4000);
    } catch (e) { toast.error(errorText(e)); } finally { setBusy(false); }
  }
  async function pull() {
    try { await api.post("/api/radio/config/pull"); toast.success("Read the radio's settings and kept a copy."); await view.reload(); } catch (e) { toast.error(errorText(e)); }
  }

  const tabs = [...(v?.sections.map((s) => ({ id: s.name, label: s.label, badge: edits[s.name] && changes.some((c) => c.section === s.name) ? <Badge tone="amber">edited</Badge> : undefined })) ?? []),
    { id: "name", label: "Name and position" }, { id: "copies", label: "Saved copies" }];
  const active = tabIds.includes(tab) ? tab : (tabIds[0] ?? "name");

  return (
    <>
      <PageHeader title="Radio settings" subtitle="What is stored on the radio itself: its frequency region, role, name, position and more. Looking is always allowed; changing is a separate switch." />
      {v && !v.connected && <p role="status" className="mb-4 rounded-lg border border-line bg-surface p-3 text-sm">No radio is connected, so there is nothing to show. Connect one on the <Link className="underline" to="/radio">Radio page</Link>.</p>}
      {v?.connected && !writable && (
        <p role="status" className="mb-4 rounded-lg border border-line bg-surface p-3 text-sm">You can look at the radio's settings but not change them. <Link className="underline" to="/settings">Turn editing on in Settings</Link> if you want it.</p>
      )}
      {v?.error && <p role="alert" className="mb-4 text-danger">{v.error}</p>}

      {v?.connected && (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <label className="sr-only" htmlFor="rc-find">Find a setting</label>
            <input id="rc-find" type="search" className="field max-w-sm" placeholder="Find a setting: region, hop, role, GPS…" value={find} onChange={(e) => setFind(e.target.value)} />
            {changes.length > 0 && <Button variant="primary" onClick={() => setReview(true)}>Review {changes.length} change{changes.length === 1 ? "" : "s"}…</Button>}
            {changes.length > 0 && <Button variant="ghost" onClick={() => { setEdits({}); setReview(false); }}>Discard</Button>}
          </div>

          {review && (
            <Card className="mb-5">
              <div role="alertdialog" aria-label="Confirm changes to the radio">
                <SectionTitle hint="Nothing has been sent yet.">These changes will be written to the radio</SectionTitle>
                <ul className="mb-3 space-y-2 text-sm">
                  {changes.map((c) => (
                    <li key={`${c.section}.${c.field}`}><b>{c.label}</b>: {show(c.old)} → {show(c.new)}{c.risk && <span className="block text-amber-text">{c.risk}</span>}</li>
                  ))}
                </ul>
                <p className="mb-3 text-sm text-muted">The current settings are copied first, so you can go back from “Saved copies”. The radio restarts to apply the change, and LoRaPedia reconnects by itself in a few seconds.</p>
                <div className="flex gap-2"><Button variant="primary" busy={busy} disabled={invalid} onClick={() => void save()}>Write to the radio</Button><Button variant="ghost" onClick={() => setReview(false)}>Back</Button></div>
              </div>
            </Card>
          )}

          {find.trim() ? (
            <Card className="mb-5">
              <SectionTitle hint={`${results.length} setting${results.length === 1 ? "" : "s"} match “${find}”.`}>Search results</SectionTitle>
              {results.length === 0 ? <p className="text-sm text-muted">No setting matches. Try a shorter word.</p> : found.slice.map(({ s, f }) => (
                <FieldRow key={`${s.name}.${f.name}`} section={s.name} f={f} edits={edits} setEdit={setEdit} disabled={!writable} tag={s.label} />
              ))}
              <Pager page={found.page} pages={found.pages} total={found.total} size={found.size} onPage={found.setPage} noun="settings" />
            </Card>
          ) : (
            <>
              <Tabs label="Radio settings sections" tabs={tabs} active={active} onChange={(id) => { setTab(id); rememberTab("radiosettings", id); }} />
              {(v.sections as RcSection[]).map((s) => (
                <TabPanel key={s.name} id={s.name} active={active}>
                  <Card className="mb-5">
                    <SectionTitle>{s.label}</SectionTitle>
                    {s.fields.map((f) => <FieldRow key={f.name} section={s.name} f={f} edits={edits} setEdit={setEdit} disabled={!writable} />)}
                  </Card>
                </TabPanel>
              ))}
              <TabPanel id="name" active={active}><NamePosition writable={writable} status={status.data} onDone={() => { void view.reload(); void status.reload(); }} /></TabPanel>
              <TabPanel id="copies" active={active}><Copies backups={v.backups} writable={writable} onDone={() => setTimeout(() => void view.reload(), 4000)} onPull={() => void pull()} /></TabPanel>
              <p className="text-sm text-muted">Not shown, on purpose: {v.excluded.map((e) => `${e.name} (${e.why})`).join("; ")}. Channels and their keys are managed in the Meshtastic app.</p>
            </>
          )}
        </>
      )}
      {v && !v.connected && <EmptyState title="Nothing to show without a radio">Settings appear here the moment a radio is connected.</EmptyState>}
    </>
  );
}
