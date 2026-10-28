import { useEffect, useRef, useState } from "react";
import { api, errorText } from "../lib/api";
import { timeAgo } from "../lib/format";
import { useSettings } from "../lib/settings";
import { useToast } from "../lib/toast";
import type { AppUpdateStatus, Settings, WikiUpdateStatus } from "../lib/types";
import { usePoll } from "../lib/usePoll";
import { Badge, Button, Card, Field, Help, SectionTitle, Toggle } from "./ui";

const ACTIVE = ["checking", "pulling", "verifying", "applying", "waiting"];

/** Settings -> Updates: the app itself (GitHub releases) and the installed Wikipedia editions (Kiwix). */
export function UpdatesCard({ draft, set }: { draft: Settings; set: (p: Partial<Settings>) => void }) {
  const toast = useToast();
  const app = usePoll(() => api.get<AppUpdateStatus>("/api/updates/status"), 5000);
  const wiki = usePoll(() => api.get<WikiUpdateStatus>("/api/kb/updates"), 15000);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [confirmLeave, setConfirmLeave] = useState(false);
  const { reload: reloadSettings } = useSettings();
  const wasRestarting = useRef(false);

  const a = app.data;
  const updaterActive = !!a?.updater && ACTIVE.includes(a.updater.state);
  const lostWhileApplying = !!app.error && a?.last_update?.result === "requested";

  // The app restarts into the new version: tell the user when it comes back.
  useEffect(() => {
    if (lostWhileApplying) wasRestarting.current = true;
    else if (wasRestarting.current && a && !app.error) {
      wasRestarting.current = false;
      toast.success(`LoRaPedia is back, running version ${a.version}.`);
    }
  }, [lostWhileApplying, a, app.error, toast]);

  async function run(key: string, fn: () => Promise<unknown>, ok?: string) {
    setBusy(key);
    try { const r = await fn(); if (ok) toast.success(ok); return r; }
    catch (e) { toast.error(errorText(e)); } finally { setBusy(null); void app.reload(); void wiki.reload(); }
  }
  const checkApp = () => run("check", async () => {
    const r = await api.post<AppUpdateStatus>("/api/updates/check");
    r.result?.ok ? toast.success(r.update_available ? `Version ${r.latest?.tag.slice(1)} is available.` : "You are up to date.") : toast.error(r.result?.message ?? "The check failed.");
  });
  const applyApp = () => { setConfirm(false); return run("apply", () => api.post("/api/updates/apply"), "Update requested. LoRaPedia will restart in a moment."); };
  const leaveBeta = () => { setConfirmLeave(false); return run("leave", async () => { await api.post("/api/updates/leave-beta"); await reloadSettings(); }, "Returning to stable. LoRaPedia will restart in a moment."); };
  const checkWiki = () => run("wcheck", async () => {
    const r = await api.post<WikiUpdateStatus>("/api/kb/updates/check");
    r.result?.ok ? toast.success(r.available.length ? "A newer Wikipedia edition is available." : "Wikipedia is up to date.") : toast.error(r.result?.message ?? "The check failed.");
  });

  return (
    <Card className="mb-5">
      <SectionTitle hint="LoRaPedia and its Wikipedia can keep themselves current. These checks are the only things it does online without being asked; turn them off to stay fully offline.">Updates</SectionTitle>

      <h3 className="mb-1 text-base font-semibold">LoRaPedia {a ? <span className="font-mono text-sm font-normal text-muted">{a.version}</span> : null}</h3>
      {a && (
        <div role="status" className="mb-3 text-sm">
          {a.update_available && a.latest ? (
            <p><Badge tone="amber">Update available</Badge> {a.latest.beta && <Badge tone="teal">Beta</Badge>} <span className="ml-1">Version <b>{a.latest.tag.slice(1)}</b> is out. <a className="underline" href={a.latest.url} target="_blank" rel="noopener noreferrer">Release notes</a></span></p>
          ) : <p className="text-muted">{a.latest ? "You are up to date." : "No release found yet."} {a.checked ? `Checked ${timeAgo(a.checked)}.` : "Not checked yet."}</p>}
          {a.latest?.notes && a.update_available && <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-bg p-3 font-mono text-xs">{a.latest.notes}</pre>}
          {a.last_error && <p className="mt-1 text-danger" role="alert">{a.last_error}</p>}
          {a.updater && a.updater.state !== "idle" && (
            <p className="mt-2 rounded-lg border border-line bg-bg p-3">
              <Badge tone={a.updater.state === "done" ? "teal" : a.updater.state === "failed" || a.updater.state === "rolled_back" ? "danger" : "amber"}>{a.updater.state.replace("_", " ")}</Badge>{" "}{a.updater.message}
            </p>
          )}
          {lostWhileApplying && <p className="mt-2 text-amber-text">Restarting into the new version… this page reconnects by itself.</p>}
          {a.updater_outdated && (
            <div role="alert" className="mt-2 rounded-lg border border-amber p-3">
              <p>The updater container is older than this version and cannot {a.last_update?.downgrade ? "return a beta to stable" : "install beta releases"} (it only knows how to move forward to stable ones), so nothing was changed. Update it first, in the project folder:</p>
              <pre className="mt-2 overflow-auto font-mono text-xs">docker compose pull updater{"\n"}docker compose up -d updater</pre>
              <p className="mt-2">Then press Update now again.</p>
            </div>
          )}
          {a.last_update && a.last_update.result !== "requested" && <p className="mt-1 text-muted">Last update: {a.last_update.from} → {a.last_update.to} ({a.last_update.result}).</p>}
          {a.update_available && a.idle_reason && a.auto_update && a.can_apply && <p className="mt-1 text-muted">An automatic update is waiting until {a.idle_reason === "a question was answered a moment ago" ? "the node has been quiet for a few minutes" : a.idle_reason}.</p>}
        </div>
      )}
      {a?.on_beta && (
        <div className="mb-3 rounded-lg border border-line bg-bg p-3 text-sm" role="region" aria-label="Return to stable">
          <p><Badge tone="teal">Beta</Badge> <span className="ml-1">You are running a test version (<span className="font-mono">{a.version}</span>).</span></p>
          {a.can_leave_beta ? (
            confirmLeave ? (
              <div className="mt-2">
                <p>This installs the newest <b>stable</b> release, which is an older version number than your beta, and stops offering betas. A backup of your settings and question log is made first and your Wikipedia library is kept. Anything the beta added (such as its Nodes data) is left in place but unused. LoRaPedia restarts for about a minute.</p>
                <div className="mt-2 flex gap-2"><Button variant="primary" busy={busy === "leave"} onClick={() => void leaveBeta()}>Return to stable</Button><Button variant="ghost" onClick={() => setConfirmLeave(false)}>Cancel</Button></div>
              </div>
            ) : (
              <div className="mt-2 flex flex-wrap items-center gap-2"><Button icon="download" disabled={updaterActive} onClick={() => setConfirmLeave(true)}>Return to stable…</Button><span className="text-muted">Updates never go backwards by themselves; this is the way back.</span></div>
            )
          ) : a.leave_beta_steps.length > 0 ? (
            <div className="mt-2"><p>To go back to stable, in the project folder:</p><pre className="mt-2 overflow-auto font-mono text-xs">{a.leave_beta_steps.join("\n")}</pre></div>
          ) : null}
        </div>
      )}
      {a && !a.can_apply && a.reason && (
        <div className="mb-3 rounded-lg border border-line bg-bg p-3 text-sm">
          <p>{a.reason}</p>
          {a.install.steps.length > 0 && <pre className="mt-2 overflow-auto font-mono text-xs">{a.install.steps.join("\n")}</pre>}
        </div>
      )}
      <div className="mb-3 flex flex-wrap gap-2">
        <Button icon="refresh" onClick={() => void checkApp()} busy={busy === "check"}>Check now</Button>
        {a?.can_apply && !confirm && <Button variant="primary" icon="download" disabled={updaterActive} onClick={() => setConfirm(true)}>Update now</Button>}
        {confirm && <span className="flex flex-wrap items-center gap-2 text-sm">LoRaPedia restarts for about a minute. A backup is made first.
          <Button variant="primary" busy={busy === "apply"} onClick={() => void applyApp()}>Update</Button><Button variant="ghost" onClick={() => setConfirm(false)}>Cancel</Button></span>}
      </div>
      <Field label="Release channel" htmlFor="up-channel" help={draft.update_channel === "beta"
        ? "Beta also receives test versions from the beta branch before they are released. They may have bugs and unfinished features. A backup of your settings and log is made before every update and a version that fails to start is rolled back, but your Wikipedia index is not backed up. Choosing Stable again never downgrades: you stay where you are until a newer stable release arrives."
        : "Stable only receives finished releases. Choose Beta to try new features early."}>
        <select id="up-channel" className="field max-w-xs" value={draft.update_channel} onChange={(e) => set({ update_channel: e.target.value as Settings["update_channel"] })}>
          <option value="stable">Stable (recommended)</option>
          <option value="beta">Beta (test versions)</option>
        </select>
      </Field>
      {a && a.channel !== draft.update_channel && <p role="status" className="mb-3 text-sm text-amber-text">Save, then press Check now to look on the {draft.update_channel} channel.</p>}
      <Toggle checked={draft.update_check} onChange={(v) => set({ update_check: v })} label="Look for new LoRaPedia releases"
        help="About once a day it asks github.com for this project's latest release. Nothing else is sent, no telemetry." />
      <Toggle checked={draft.auto_update} onChange={(v) => set({ auto_update: v })} label="Install them automatically"
        help={`Only in a Docker install with the updater container. It waits until the node has been quiet for a few minutes, verifies the release's signature, and rolls back if the new version does not start. ${a && !a.can_apply && a.install.kind !== "docker" ? "This installation cannot update itself, so this has no effect here." : ""}`} />

      <hr className="my-5 border-line" />
      <h3 className="mb-1 text-base font-semibold">Wikipedia</h3>
      {wiki.data && (
        <div className="mb-3 text-sm" role="status">
          {wiki.data.available.length === 0
            ? <p className="text-muted">{wiki.data.checked ? `Your Wikipedia is current. Checked ${timeAgo(wiki.data.checked)}.` : "Not checked yet."}</p>
            : <ul className="space-y-2">{wiki.data.available.map((u) => (
              <li key={u.package} className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-bg p-3">
                <Badge tone="amber">New edition</Badge><span><b>{u.name}</b> {u.installed} → {u.latest}</span>
                <span className="text-xs text-muted">≈ {(u.size_mb / 1000).toFixed(1)} GB download, {(u.needs_mb / 1000).toFixed(1)} GB free needed</span>
                <Button className="ml-auto" icon="download" busy={busy === `w-${u.package}`} onClick={() => void run(`w-${u.package}`, () => api.post(`/api/kb/packages/${u.package}/update`), "Update started. The old edition is removed once the new one is ready.")}>Update now</Button>
              </li>))}</ul>}
          {wiki.data.error && <p className="mt-1 text-danger" role="alert">{wiki.data.error}</p>}
          {Object.entries(wiki.data.last).filter(([, l]) => l.result === "skipped").map(([k, l]) => <p key={k} className="mt-1 text-amber-text">An automatic update was not started: {l.message}</p>)}
        </div>
      )}
      <div className="mb-3"><Button icon="refresh" onClick={() => void checkWiki()} busy={busy === "wcheck"}>Check Wikipedia now</Button></div>
      <Toggle checked={draft.wikipedia_update_check} onChange={(v) => set({ wikipedia_update_check: v })} label="Look for newer Wikipedia editions"
        help="About once a day it asks download.kiwix.org which edition is newest. Kiwix publishes roughly monthly." />
      <Toggle checked={draft.wikipedia_auto_update} onChange={(v) => set({ wikipedia_auto_update: v })} label="Update Wikipedia automatically"
        help="Downloads and indexes the new edition beside the old one, and removes the old one only when the new one is ready. Only when the node is quiet and the disk has room. Only the packages you installed, and never rebuilds your index on its own." />
      <Help>Updating Wikipedia takes a while (a download, then re-indexing). Questions keep being answered meanwhile.</Help>
    </Card>
  );
}
