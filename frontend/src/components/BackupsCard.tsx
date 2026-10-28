import { useRef, useState } from "react";
import { api, errorText, upload } from "../lib/api";
import { fmtBytes, timeAgo } from "../lib/format";
import { useToast } from "../lib/toast";
import type { BackupItem, BackupStatus, Settings } from "../lib/types";
import { usePoll } from "../lib/usePoll";
import { Badge, Button, Card, EmptyState, Field, SectionTitle, Toggle } from "./ui";

const KIND: Record<BackupItem["kind"], { label: string; tone: "muted" | "teal" | "amber" }> = {
  manual: { label: "Manual", tone: "teal" },
  auto: { label: "Daily", tone: "muted" },
  "pre-update": { label: "Before an update", tone: "muted" },
  "before-restore": { label: "Before a restore", tone: "amber" },
  uploaded: { label: "Uploaded", tone: "teal" },
};

const linkButton = "inline-flex items-center justify-center gap-2 rounded-lg border border-line bg-raised px-3.5 py-2 text-sm hover:border-muted";

/** Settings -> Backups: copies of the settings and question log, made, downloaded, uploaded and restored from here. */
export function BackupsCard({ draft, set }: { draft: Settings; set: (p: Partial<Settings>) => void }) {
  const toast = useToast();
  const status = usePoll(() => api.get<BackupStatus>("/api/backups"), 30000);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{ name: string; action: "restore" | "delete" } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const items = status.data?.items ?? [];

  async function run(key: string, fn: () => Promise<unknown>, ok: string) {
    setBusy(key);
    try { await fn(); toast.success(ok); }
    catch (e) { toast.error(errorText(e)); }
    finally { setBusy(null); setConfirm(null); void status.reload(); }
  }
  const backUp = () => run("make", () => api.post("/api/backups"), "Backup saved.");
  const restore = (name: string) => run(name, async () => {
    const r = await api.post<{ saved_previous_as: string }>(`/api/backups/${name}/restore`);
    toast.info(`Your previous settings and log were saved as ${r.saved_previous_as}.`);
    setTimeout(() => window.location.reload(), 2500);  // every page is showing the old settings: start from the restored ones
  }, "Restored. Reloading to show them. Some settings, such as the radio connection, take effect when LoRaPedia next reconnects or restarts.");
  const remove = (name: string) => run(name, () => api.del(`/api/backups/${name}`), "Backup deleted.");
  const onFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (file) void run("upload", () => upload("/api/backups/upload", file), "Backup added to the list. Restore it when you are ready.");
  };

  return (
    <Card className="mb-5">
      <SectionTitle hint="A backup holds your settings and the question log. It does not hold Wikipedia or its search index (those are large and can be downloaded and rebuilt).">Backups</SectionTitle>

      <Toggle checked={draft.auto_backup} onChange={(v) => set({ auto_backup: v })} label="Back up once a day"
        help="Taken while LoRaPedia runs, and only the newest few are kept. A backup is also taken before every update and before every restore." />
      {draft.auto_backup && (
        <div className="ml-8 max-w-xs">
          <Field label="Daily backups to keep" htmlFor="bk-keep"><input id="bk-keep" type="number" min={1} max={60} className="field" value={draft.backup_keep} onChange={(e) => set({ backup_keep: Number(e.target.value) })} /></Field>
        </div>
      )}

      <div className="mb-3 mt-2 flex flex-wrap gap-2">
        <Button variant="primary" icon="download" busy={busy === "make"} onClick={() => void backUp()}>Back up now</Button>
        <Button icon="upload" busy={busy === "upload"} onClick={() => fileInput.current?.click()}>Add a backup file…</Button>
        <input ref={fileInput} type="file" accept=".db,application/vnd.sqlite3,application/octet-stream" className="sr-only" aria-label="Backup file to add" onChange={onFile} />
      </div>

      {status.error && <p role="alert" className="text-sm text-danger">{errorText(status.error)}</p>}
      {status.data && items.length === 0 && <EmptyState title="No backups yet">Press "Back up now", or leave the daily backup on.</EmptyState>}
      {items.length > 0 && (
        <ul className="divide-y divide-line rounded-lg border border-line" aria-label="Backups">
          {items.map((b) => (
            <li key={b.name} className="p-3">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <Badge tone={KIND[b.kind].tone}>{KIND[b.kind].label}</Badge>
                <span className="text-sm" title={new Date(b.ts * 1000).toLocaleString()}>{timeAgo(b.ts)}</span>
                <span className="text-sm text-muted">{fmtBytes(b.size)}</span>
                <span className="ml-auto flex flex-wrap gap-2">
                  <a className={linkButton} href={`/api/backups/${b.name}/download`} download>Download</a>
                  <Button onClick={() => setConfirm({ name: b.name, action: "restore" })}>Restore…</Button>
                  <Button variant="ghost" icon="trash" aria-label={`Delete backup from ${new Date(b.ts * 1000).toLocaleString()}`} onClick={() => setConfirm({ name: b.name, action: "delete" })} />
                </span>
              </div>
              {confirm?.name === b.name && (
                <div role="alertdialog" aria-label="Confirm" className="mt-3 rounded-lg border border-line bg-bg p-3 text-sm">
                  {confirm.action === "restore" ? (
                    <>
                      <p>Replace your current settings and question log with this backup? What you have now is saved first, so you can undo it.</p>
                      <div className="mt-2 flex gap-2">
                        <Button variant="primary" busy={busy === b.name} onClick={() => void restore(b.name)}>Restore this backup</Button>
                        <Button onClick={() => setConfirm(null)}>Cancel</Button>
                      </div>
                    </>
                  ) : (
                    <>
                      <p>Delete this backup? This cannot be undone.</p>
                      <div className="mt-2 flex gap-2">
                        <Button variant="danger" busy={busy === b.name} onClick={() => void remove(b.name)}>Delete</Button>
                        <Button onClick={() => setConfirm(null)}>Cancel</Button>
                      </div>
                    </>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-sm text-muted">
        Backups are kept in the <span className="font-mono">{status.data?.folder ?? "backups"}</span> folder next to your data{status.data ? ` (${fmtBytes(status.data.total_size)} in all)` : ""}. They protect against a bad change or update, not against losing the disk, so download one now and then and keep it somewhere else. A backup contains your question log.
      </p>
    </Card>
  );
}
