import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError, errorText } from "../lib/api";
import { useToast } from "../lib/toast";
import type { WikiPackage } from "../lib/types";
import { usePoll } from "../lib/usePoll";
import { Badge, Button, Card, Progress, SectionTitle } from "./ui";

const fmtSize = (mb: number) => (mb >= 1000 ? `${(mb / 1000).toFixed(mb >= 10000 ? 0 : 1)} GB` : `${mb} MB`);

/** One-click Wikipedia: pick a package and it downloads, joins the library and is indexed automatically. */
export function WikipediaPackages({ onChanged }: { onChanged?: () => void }) {
  const toast = useToast();
  const nav = useNavigate();
  const pk = usePoll(() => api.get<WikiPackage[]>("/api/kb/packages"), 1500);
  const info = usePoll(() => api.get<{ disk_free_gb: number }>("/api/system/info"), 30000);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const list = pk.data ?? [];
  const installed = list.filter((p) => ["ready", "downloaded", "indexing"].includes(p.state));
  const free = info.data?.disk_free_gb;

  async function install(p: WikiPackage, replace: boolean) {
    setConfirm(null); setBusy(p.id);
    try { await api.post(`/api/kb/packages/${p.id}/install`, { replace }); await pk.reload(); onChanged?.(); }
    catch (e) {
      const msg = errorText(e);
      if (e instanceof ApiError && e.status === 409 && /rebuild/.test(msg)) {
        toast.error(msg, [{ label: "Rebuild and install", run: () => void install(p, true) }]); // the index must be rebuilt: ask first
      } else {
        toast.error(msg, /mbedding/.test(msg) ? [{ label: "Open Model page", run: () => nav("/model") }] : undefined);
      }
    } finally { setBusy(null); }
  }
  const control = (p: WikiPackage, a: "pause" | "resume" | "cancel") =>
    api.post(`/api/kb/downloads/${encodeURIComponent(p.filename ?? "")}/${a}`).then(() => pk.reload()).catch((e) => toast.error(errorText(e)));

  return (
    <Card>
      <SectionTitle hint="Pick one. LoRaPedia downloads it, adds it to the library, and builds the search index for you. The newest version is looked up when you press Install.">
        Wikipedia
      </SectionTitle>
      <ul className="grid gap-3 xl:grid-cols-2">
        {list.map((p) => {
          const tooBig = free !== undefined && (p.approx_mb * 1.3) / 1000 > free && p.state === "available";
          const others = installed.filter((o) => o.id !== p.id);
          const pct = Math.round(p.progress * 100);
          return (
            <li key={p.id} className={`rounded-xl border p-4 ${p.recommended && p.state === "available" ? "border-amber/60" : "border-line"} bg-bg`}>
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-lg font-semibold">{p.name}</h3>
                {p.recommended && <Badge tone="amber">Recommended</Badge>}
                <span className="ml-auto font-mono text-sm text-muted">≈ {fmtSize(p.approx_mb)}</span>
              </div>
              <p className="mt-1 text-sm text-muted">{p.blurb}</p>
              <p className="mt-1 text-xs text-muted">{p.good_for}</p>

              <div className="mt-3">
                {p.state === "available" && (confirm === p.id ? (
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span>Replace {others.map((o) => o.name).join(", ")}?</span>
                    <Button variant="primary" busy={busy === p.id} onClick={() => void install(p, true)}>Replace it</Button>
                    <Button onClick={() => void install(p, false)}>Keep both</Button>
                    <Button variant="ghost" onClick={() => setConfirm(null)}>Cancel</Button>
                  </div>
                ) : (
                  <div className="flex flex-wrap items-center gap-2">
                    <Button variant={p.recommended && others.length === 0 ? "primary" : "secondary"} icon="download" busy={busy === p.id} disabled={tooBig}
                      onClick={() => (others.length ? setConfirm(p.id) : void install(p, false))}>
                      {others.length ? "Install instead" : "Install"}
                    </Button>
                    {tooBig && <Badge tone="danger">Needs more disk space ({free} GB free)</Badge>}
                  </div>
                ))}

                {(p.state === "downloading" || p.state === "paused") && (
                  <div role="status">
                    <div className="mb-1 flex items-center gap-2 text-sm"><Badge tone="amber">{p.state === "paused" ? "Paused" : "Downloading"}</Badge><span className="font-mono text-xs text-muted">{pct}%</span>
                      <span className="ml-auto flex gap-1">
                        {p.state === "downloading" ? <Button variant="ghost" icon="pause" onClick={() => void control(p, "pause")}>Pause</Button> : <Button variant="ghost" icon="play" onClick={() => void control(p, "resume")}>Resume</Button>}
                        <Button variant="ghost" icon="x" onClick={() => void control(p, "cancel")}>Cancel</Button></span></div>
                    <Progress value={pct} label={`Downloading ${p.name}`} tone="teal" />
                    <p className="mt-1 text-xs text-muted">Next: it is indexed automatically.</p>
                  </div>
                )}

                {p.state === "indexing" && (
                  <div role="status">
                    <div className="mb-1 flex items-center gap-2 text-sm"><Badge tone="amber">Making it searchable</Badge><span className="font-mono text-xs text-muted">{pct}%</span></div>
                    <Progress value={pct} label={`Indexing ${p.name}`} />
                    <p className="mt-1 text-xs text-muted">You can ask questions meanwhile; answers improve as it goes.</p>
                  </div>
                )}

                {p.state === "ready" && <p className="flex items-center gap-2 text-sm"><Badge tone="teal">Installed</Badge><span className="text-muted">{p.articles.toLocaleString()} articles searchable</span></p>}
                {p.state === "downloaded" && <p className="flex items-center gap-2 text-sm"><Badge>In library</Badge><span className="text-muted">Not indexed yet. Use Indexing below.</span></p>}

                {p.state === "error" && (
                  <div role="alert" className="text-sm"><p className="text-danger">{p.detail ?? "Something went wrong."}</p>
                    <Button className="mt-2" icon="refresh" onClick={() => void install(p, others.length > 0)}>Try again (resumes where it stopped)</Button></div>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-xs text-muted">Sizes are approximate and include only the download; the search index needs extra room. Images are never used, so image-heavy editions are not offered. Only one is needed: more articles means a slower index, not necessarily better answers.</p>
    </Card>
  );
}
