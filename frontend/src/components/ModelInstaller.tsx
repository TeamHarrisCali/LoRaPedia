import { useState } from "react";
import { api, errorText } from "../lib/api";
import { fmtBytes } from "../lib/format";
import { useToast } from "../lib/toast";
import type { HardwareInfo, InstalledModel, PullJob } from "../lib/types";
import { usePoll } from "../lib/usePoll";
import { Badge, Button, Progress } from "./ui";

/** Suggested models for this hardware, with install (pull) progress. Pulling is the only step that needs internet. */
export function ModelInstaller({ kind, current, onChoose }: { kind: "llm" | "embed"; current: string; onChoose: (tag: string) => void }) {
  const toast = useToast();
  const hw = usePoll(() => api.get<HardwareInfo>("/api/system/hardware"), 3_600_000);
  const models = usePoll(() => api.get<{ installed: InstalledModel[]; error: string | null }>("/api/models"), 5000);
  const pulls = usePoll(() => api.get<PullJob[]>("/api/models/pulls"), 1000);
  const [custom, setCustom] = useState("");

  const installed = new Set((models.data?.installed ?? []).map((m) => m.name));
  const isInstalled = (tag: string) => installed.has(tag) || installed.has(`${tag}:latest`);
  const job = (tag: string) => pulls.data?.find((j) => j.tag === tag);
  const list = kind === "llm" ? hw.data?.suggestions.llm : hw.data?.suggestions.embed;

  async function pull(tag: string) {
    try { await api.post("/api/models/pull", { tag }); void pulls.reload(); }
    catch (e) { toast.error(errorText(e)); }
  }

  const row = (tag: string, size: number | null, blurb: string, extra?: React.ReactNode) => {
    const j = job(tag);
    const running = j?.state === "running";
    return (
      <li key={tag} className="rounded-lg border border-line bg-bg p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-semibold">{tag}</span>
          {size != null && <span className="text-xs text-muted">{size < 1 ? `${Math.round(size * 1000)} MB` : `${size} GB`} download</span>}
          {extra}
          <span className="ml-auto flex items-center gap-2">
            {isInstalled(tag) && <Badge tone="teal">Installed</Badge>}
            {/* "In use" only when it is really there; a configured-but-missing model must say so. */}
            {current === tag && !isInstalled(tag) && <Badge tone="danger">Selected, not installed</Badge>}
            {current === tag && isInstalled(tag)
              ? <Badge tone="amber">In use</Badge>
              : isInstalled(tag)
                ? <Button onClick={() => onChoose(tag)}>Use this</Button>
                : <Button icon="download" busy={running} onClick={() => void pull(tag)}>Install</Button>}
          </span>
        </div>
        {blurb && <p className="mt-1 text-sm text-muted">{blurb}</p>}
        {running && (
          <div className="mt-2">
            <Progress value={j!.completed} max={j!.total || 1} label={`Downloading ${tag}`} />
            <p className="mt-1 font-mono text-xs text-muted" role="status">{j!.total ? `${fmtBytes(j!.completed)} of ${fmtBytes(j!.total)}` : j!.status}</p>
          </div>
        )}
        {j?.state === "error" && <p className="mt-2 text-sm text-danger" role="alert">{j.error} <button className="underline" onClick={() => void pull(tag)}>Retry</button></p>}
      </li>
    );
  };

  return (
    <div>
      {models.data?.error && (
        <p className="mb-3 rounded-lg border border-danger/60 p-3 text-sm" role="alert">
          {models.data.error} Install Ollama on this machine (it is a one-time download from ollama.com), start it, then press Retry.
          <Button className="ml-3" icon="refresh" onClick={() => void models.reload()}>Retry</Button>
        </p>
      )}
      {hw.data && <p className="mb-3 text-sm text-muted">{hw.data.suggestions.note}</p>}
      <ul className="space-y-2">
        {list?.map((m) => row(m.tag, m.size_gb, m.blurb, <>{m.recommended && <Badge tone="amber">Recommended</Badge>}{m.fits === false && <Badge tone="danger">May be too big</Badge>}</>))}
        {/* Anything already installed that is not a suggestion (e.g. a custom fine-tune) can still be chosen. */}
        {kind === "llm" && (models.data?.installed ?? []).filter((m) => !list?.some((s) => s.tag === m.name)).map((m) => row(m.name, m.size_gb, m.params ? `${m.params} parameters` : ""))}
      </ul>
      <form className="mt-3 flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); if (custom.trim()) { void pull(custom.trim()); setCustom(""); } }}>
        <label className="sr-only" htmlFor={`custom-${kind}`}>Other model name</label>
        <input id={`custom-${kind}`} className="field max-w-xs font-mono" placeholder="Other model, e.g. qwen2.5:3b" value={custom} onChange={(e) => setCustom(e.target.value)} />
        <Button type="submit" icon="download">Install</Button>
      </form>
    </div>
  );
}
