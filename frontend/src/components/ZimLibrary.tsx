import { useState } from "react";
import { api, errorText } from "../lib/api";
import { fmtBytes, fmtDuration, timeAgo } from "../lib/format";
import { useToast } from "../lib/toast";
import type { CatalogEntry, DownloadJob, Zim } from "../lib/types";
import { usePoll } from "../lib/usePoll";
import { Badge, Button, Card, EmptyState, Progress, SectionTitle } from "./ui";

const STATUS_TONE: Record<string, "teal" | "amber" | "danger" | "muted"> = { done: "teal", running: "amber", error: "danger", stopped: "amber" };

/** Wikipedia files: what is installed, adding more (local file, sample, or optional download), and removing. */
export function ZimLibrary({ onChanged }: { onChanged?: () => void }) {
  const toast = useToast();
  const lib = usePoll(() => api.get<Zim[]>("/api/kb/library"), 4000);
  const dls = usePoll(() => api.get<DownloadJob[]>("/api/kb/downloads"), 1000);
  const [path, setPath] = useState("");
  const [url, setUrl] = useState("");
  const [catalog, setCatalog] = useState<CatalogEntry[] | null>(null);
  const [loadingCatalog, setLoadingCatalog] = useState(false);
  const [confirm, setConfirm] = useState<string | null>(null);

  const changed = () => { void lib.reload(); onChanged?.(); };
  async function run<T>(fn: () => Promise<T>, ok?: string) {
    try { const r = await fn(); if (ok) toast.success(ok); changed(); return r; } catch (e) { toast.error(errorText(e)); }
  }
  async function loadCatalog() {
    setLoadingCatalog(true);
    try { setCatalog(await api.get<CatalogEntry[]>("/api/kb/catalog")); }
    catch (e) { toast.error(errorText(e), [{ label: "Retry", run: () => void loadCatalog() }]); }
    finally { setLoadingCatalog(false); }
  }
  const download = (u: string) => run(() => api.post("/api/kb/downloads", { url: u }), "Download started.").then(() => void dls.reload());
  const act = (id: string, a: string) => run(() => api.post(`/api/kb/downloads/${encodeURIComponent(id)}/${a}`)).then(() => void dls.reload());

  return (
    <Card>
      <SectionTitle hint="Wikipedia snapshots in ZIM format. The smaller 'top mini' file is plenty to start with.">Library</SectionTitle>

      {lib.data && lib.data.length === 0 ? (
        <EmptyState title="No Wikipedia files yet">Add one below. Not sure? Try the 12-article sample to see everything work, then add the real thing.</EmptyState>
      ) : (
        <ul className="space-y-2">
          {lib.data?.map((z) => (
            <li key={z.name} className="flex flex-wrap items-center gap-3 rounded-lg border border-line bg-bg p-3">
              <div className="min-w-0 flex-1">
                <p className="truncate font-semibold">{z.title || z.name}</p>
                <p className="truncate font-mono text-xs text-muted">{z.name} · {z.articles.toLocaleString()} articles in file</p>
                <p className="text-xs text-muted">{z.indexed_articles > 0 ? `${z.indexed_articles.toLocaleString()} indexed (${z.indexed_chunks.toLocaleString()} chunks), last ${timeAgo(z.last_indexed)}` : "Not indexed yet"}</p>
                {z.error && <p className="text-xs text-danger" role="alert">{z.error}</p>}
              </div>
              <Badge tone={STATUS_TONE[z.status] ?? "muted"}>{z.status}</Badge>
              {confirm === z.name ? (
                <span className="flex items-center gap-2 text-sm">Remove and forget its index?
                  <Button variant="danger" onClick={() => { setConfirm(null); void run(() => api.del(`/api/kb/library/${encodeURIComponent(z.name)}`), "Removed. The file itself was not deleted."); }}>Yes, remove</Button>
                  <Button variant="ghost" onClick={() => setConfirm(null)}>Cancel</Button></span>
              ) : <Button variant="ghost" icon="trash" onClick={() => setConfirm(z.name)} aria-label={`Remove ${z.name}`}>Remove</Button>}
            </li>
          ))}
        </ul>
      )}

      <div className="mt-5 grid gap-5 xl:grid-cols-2">
        <div>
          <h3 className="mb-2 text-base font-semibold">Use a file you already have</h3>
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (path.trim()) void run(() => api.post("/api/kb/library/import", { path: path.trim() }), "Added to the library.").then(() => setPath("")); }}>
            <label className="sr-only" htmlFor="zim-path">Path to a ZIM file</label>
            <input id="zim-path" className="field font-mono" placeholder="/path/to/wikipedia_en_top_mini.zim" value={path} onChange={(e) => setPath(e.target.value)} />
            <Button type="submit">Import</Button>
          </form>
          <p className="mt-1 text-sm text-muted">Or copy files into the <span className="font-mono">zim</span> folder inside the data directory and restart.</p>
          <Button className="mt-3" onClick={() => void run(() => api.post("/api/kb/sample"), "Sample added: 12 articles.")}>Try the 12-article sample</Button>
        </div>

        <div>
          <h3 className="mb-2 flex items-center gap-2 text-base font-semibold">Download from Kiwix <Badge tone="amber">Optional · needs internet</Badge></h3>
          <p className="mb-2 text-sm text-muted">The only part of LoRaPedia that goes online, and only when you press a button here. Downloads resume if interrupted.</p>
          <Button icon="download" onClick={() => void loadCatalog()} busy={loadingCatalog}>Browse Wikipedia files</Button>
          <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); if (url.trim()) void download(url.trim()).then(() => setUrl("")); }}>
            <label className="sr-only" htmlFor="zim-url">ZIM download link</label>
            <input id="zim-url" className="field font-mono" placeholder="https://…/something.zim" value={url} onChange={(e) => setUrl(e.target.value)} />
            <Button type="submit">Download</Button>
          </form>
        </div>
      </div>

      {catalog && (
        <ul className="mt-4 space-y-2">
          {catalog.length === 0 && <li className="text-sm text-muted">The catalog came back empty. Paste a link instead.</li>}
          {catalog.map((c) => (
            <li key={c.url} className="flex flex-wrap items-center gap-3 rounded-lg border border-line bg-bg p-3">
              <div className="min-w-0 flex-1"><p className="font-mono text-sm font-semibold">{c.variant.replace("_", " ")} <span className="text-xs font-normal text-muted">{c.date}</span></p><p className="text-sm text-muted">{c.description}</p></div>
              <span className="font-mono text-sm">{c.size}B</span>
              <Button icon="download" onClick={() => void download(c.url)}>Download</Button>
            </li>
          ))}
        </ul>
      )}

      {dls.data && dls.data.length > 0 && (
        <div className="mt-5"><h3 className="mb-2 text-base font-semibold">Downloads</h3>
          <ul className="space-y-2">
            {dls.data.map((d) => (
              <li key={d.id} className="rounded-lg border border-line bg-bg p-3">
                <div className="flex flex-wrap items-center gap-2"><span className="font-mono text-sm">{d.filename}</span><Badge tone={d.state === "done" ? "teal" : d.state === "error" ? "danger" : "amber"}>{d.state}</Badge>
                  <span className="ml-auto flex gap-1">
                    {d.state === "running" && <Button variant="ghost" icon="pause" onClick={() => void act(d.id, "pause")}>Pause</Button>}
                    {(d.state === "paused" || d.state === "error") && <Button variant="ghost" icon="play" onClick={() => void act(d.id, "resume")}>Resume</Button>}
                    {(d.state === "running" || d.state === "paused" || d.state === "error") && <Button variant="ghost" icon="x" onClick={() => void act(d.id, "cancel")}>Cancel</Button>}
                  </span></div>
                {d.state !== "done" && <>
                  <div className="my-2"><Progress value={d.bytes} max={d.total || 1} label={`Downloading ${d.filename}`} tone="teal" /></div>
                  <p className="font-mono text-xs text-muted" role="status">{fmtBytes(d.bytes)}{d.total ? ` of ${fmtBytes(d.total)}` : ""}{d.state === "running" && d.speed > 0 ? ` · ${fmtBytes(d.speed)}/s · ${fmtDuration(d.eta_s)} left` : ""}</p>
                </>}
                {d.error && <p className="mt-1 text-sm text-danger" role="alert">{d.error}</p>}
              </li>
            ))}
          </ul></div>
      )}
    </Card>
  );
}
