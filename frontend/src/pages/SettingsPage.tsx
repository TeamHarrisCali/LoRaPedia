import { ChangeEvent, useState } from "react";
import { SaveBar } from "../components/SaveBar";
import { BackupsCard } from "../components/BackupsCard";
import { UpdatesCard } from "../components/UpdatesCard";
import { Button, Card, Field, PageHeader, SectionTitle, Toggle } from "../components/ui";
import { api, errorText } from "../lib/api";
import { useDraft, useSettings } from "../lib/settings";
import { applyTheme, getThemePref, ThemePref } from "../lib/theme";
import { useToast } from "../lib/toast";
import { usePoll } from "../lib/usePoll";

const linkButton = "inline-flex items-center justify-center gap-2 rounded-lg border border-line bg-raised px-3.5 py-2 text-sm hover:border-muted";

export function SettingsPage() {
  const toast = useToast();
  const { save } = useSettings();
  const { draft, set, dirty, save: saveDraft, discard } = useDraft();
  const info = usePoll(() => api.get<{ version: string; data_dir: string; disk_free_gb: number }>("/api/system/info"), 60000);
  const [theme, setTheme] = useState<ThemePref>(getThemePref());
  if (!draft) return <p className="text-muted" role="status">Loading…</p>;

  async function restore(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      if (await save(parsed)) toast.success("Settings restored from backup.");
    } catch (err) { toast.error(err instanceof SyntaxError ? "That file is not a LoRaPedia settings backup." : errorText(err)); }
  }

  async function seed() {
    try { const r = await api.post<{ added: number }>("/api/demo/seed"); toast.success(`Added ${r.added} demo conversations.`); }
    catch (e) { toast.error(errorText(e)); }
  }

  return (
    <>
      <PageHeader title="Settings" subtitle="Everything here can be changed without restarting." />

      <Card className="mb-5">
        <SectionTitle hint="Stops one person from monopolising the radio.">Rate limits</SectionTitle>
        <div className="grid gap-x-6 sm:grid-cols-3">
          <Field label="Questions allowed" htmlFor="rl-n" help="Per node, per time window. 0 turns limiting off."><input id="rl-n" type="number" min={0} className="field" value={draft.rate_limit_queries} onChange={(e) => set({ rate_limit_queries: Number(e.target.value) })} /></Field>
          <Field label="Time window (seconds)" htmlFor="rl-w" help="1 question per 60 seconds by default."><input id="rl-w" type="number" min={1} className="field" value={draft.rate_limit_window_s} onChange={(e) => set({ rate_limit_window_s: Number(e.target.value) })} /></Field>
          <Field label="Longest queue" htmlFor="rl-q" help="When this many questions are waiting across everyone, new ones get a 'busy' reply."><input id="rl-q" type="number" min={1} className="field" value={draft.queue_max} onChange={(e) => set({ queue_max: Number(e.target.value) })} /></Field>
        </div>
        <Toggle checked={draft.queue_when_limited} onChange={(v) => set({ queue_when_limited: v })} label="Hold questions that come too soon"
          help="A question sent inside someone's cooldown is kept and answered when its turn comes, instead of getting a 'busy, try again' reply." />
        {draft.queue_when_limited && (
          <div className="ml-8 grid gap-x-6 sm:grid-cols-2">
            <Field label="Most waiting per node" htmlFor="rl-mq" help="One person cannot hold more than this many questions at once; beyond it they get a 'busy' reply."><input id="rl-mq" type="number" min={1} max={10} className="field" value={draft.max_queued_per_node} onChange={(e) => set({ max_queued_per_node: Number(e.target.value) })} /></Field>
            <Toggle checked={draft.ack_queued} onChange={(v) => set({ ack_queued: v })} label="Tell them it was queued"
              help="Sends a short 'Queued. Answer in about 45s.' so the asker knows it arrived. Turn off to save airtime." />
          </div>
        )}
      </Card>

      <Card className="mb-5">
        <SectionTitle>Answers</SectionTitle>
        <Toggle checked={draft.ai_fallback} onChange={(v) => set({ ai_fallback: v })} label="Answer from the AI's memory when Wikipedia has nothing"
          help="Only once the whole library is indexed, so 'not found' is certain. The reply starts with [AI memory - may be wrong]. Small models do get facts wrong: turn this off if you would rather hear 'not found'." />
      </Card>

      <Card className="mb-5">
        <SectionTitle>Appearance</SectionTitle>
        <fieldset>
          <legend className="sr-only">Theme</legend>
          <div className="flex flex-wrap gap-4">
            {(["dark", "light", "system"] as ThemePref[]).map((t) => (
              <label key={t} className="flex cursor-pointer items-center gap-2 text-sm capitalize">
                <input type="radio" name="theme" checked={theme === t} onChange={() => { setTheme(t); applyTheme(t); }} />{t === "system" ? "Match my device" : t === "dark" ? "Dark (field radio)" : "Light (paper)"}
              </label>
            ))}
          </div>
        </fieldset>
      </Card>

      <UpdatesCard draft={draft} set={set} />

      <BackupsCard draft={draft} set={set} />

      <Card className="mb-5">
        <SectionTitle hint="Where LoRaPedia keeps its databases and Wikipedia files.">Data</SectionTitle>
        <dl className="mb-3 grid gap-1 text-sm sm:grid-cols-[10rem_1fr]">
          <dt className="text-muted">Data directory</dt><dd className="break-all font-mono">{info.data?.data_dir ?? "…"}</dd>
          <dt className="text-muted">Free disk space</dt><dd>{info.data ? `${info.data.disk_free_gb} GB` : "…"}</dd>
        </dl>
        <p className="mb-3 text-sm text-muted">To move it, set <span className="font-mono">LORAPEDIA_DATA</span> (or change the volume in docker-compose.yml) and restart. It cannot be moved while running.</p>
        <div className="flex flex-wrap gap-2">
          <a className={linkButton} href="/api/queries/export.csv" download>Export logs (CSV)</a>
          <a className={linkButton} href="/api/settings/backup" download>Back up settings</a>
          <label className={`${linkButton} cursor-pointer`}>Restore settings<input type="file" accept="application/json,.json" className="sr-only" onChange={restore} /></label>
        </div>
      </Card>

      <Card className="mb-5">
        <SectionTitle hint="Fills the Live Feed and Dashboard with sample conversations so you can see how they look.">Demo data</SectionTitle>
        <Button onClick={seed}>Load demo conversations</Button>
      </Card>

      <p className="text-sm text-muted">LoRaPedia {info.data?.version ?? ""} · Offline unless you press a download button or leave update checks on · No telemetry</p>
      <SaveBar dirty={dirty} onSave={() => void saveDraft()} onDiscard={discard} />
    </>
  );
}
