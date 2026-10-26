import { useEffect, useState } from "react";
import { api, errorText } from "../lib/api";
import { fmtDuration } from "../lib/format";
import { useToast } from "../lib/toast";
import type { IndexProgress, Zim } from "../lib/types";
import { usePoll } from "../lib/usePoll";
import { Badge, Button, Card, Field, Help, Progress, SectionTitle } from "./ui";

const SIZES = [["all", "All articles"], ["10000", "First 10,000 articles"], ["100000", "First 100,000 articles"]] as const;

/** Start, watch, pause and resume indexing. A small subset first gives a working system fast; expand later. */
export function IndexControls({ preferredZim, onDone }: { preferredZim?: string; onDone?: () => void }) {
  const toast = useToast();
  const lib = usePoll(() => api.get<Zim[]>("/api/kb/library"), 5000);
  const [running, setRunning] = useState(false);
  const prog = usePoll(() => api.get<IndexProgress>("/api/kb/index/status"), 1000);
  const [zim, setZim] = useState("");
  const [size, setSize] = useState<(typeof SIZES)[number][0]>("all");
  const [leadOnly, setLeadOnly] = useState(false);

  const p = prog.data;
  const active = p?.state === "running" || p?.state === "paused";
  useEffect(() => { if (!zim && lib.data?.length) setZim(preferredZim && lib.data.some((z) => z.name === preferredZim) ? preferredZim : lib.data[0].name); }, [lib.data, zim, preferredZim]);
  useEffect(() => { // announce completion / failure once
    if (running && p?.state === "done") { setRunning(false); toast.success(`Indexing finished: ${p.articles.toLocaleString()} articles.`); onDone?.(); }
    if (running && p?.state === "error") { setRunning(false); toast.error(p.error ?? "Indexing stopped.", [{ label: "Resume", run: () => void start() }]); }
  }, [p?.state]); // eslint-disable-line react-hooks/exhaustive-deps

  async function start() {
    try {
      setRunning(true);
      await api.post("/api/kb/index/start", { zim, max_articles: size === "all" ? null : Number(size), lead_only: leadOnly });
      void prog.reload();
    } catch (e) { setRunning(false); toast.error(errorText(e)); }
  }
  const control = (a: "pause" | "resume" | "stop") => api.post(`/api/kb/index/${a}`).then(() => prog.reload()).catch((e) => toast.error(errorText(e)));
  const pct = p && p.entries_total ? (p.entries_done / p.entries_total) * 100 : 0;

  if (lib.data && lib.data.length === 0) return <Card><SectionTitle>Indexing</SectionTitle><p className="text-sm text-muted">Add a Wikipedia file first, then come back here.</p></Card>;
  return (
    <Card>
      <SectionTitle hint="Reads the Wikipedia file and builds the search index. You can answer questions from the part that is done while the rest continues.">Indexing</SectionTitle>
      {!active && (
        <div className="grid gap-x-6 xl:grid-cols-3">
          <Field label="Wikipedia file" htmlFor="ix-zim"><select id="ix-zim" className="field" value={zim} onChange={(e) => setZim(e.target.value)}>{lib.data?.map((z) => <option key={z.name} value={z.name}>{z.title || z.name}</option>)}</select></Field>
          <Field label="How many" htmlFor="ix-size" help="Taken in file order. Start small, expand later.">
            <select id="ix-size" className="field" value={size} onChange={(e) => setSize(e.target.value as typeof size)}>{SIZES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
          <Field label="How much of each" htmlFor="ix-detail" help="Lead only is fastest and smallest.">
            <select id="ix-detail" className="field" value={leadOnly ? "lead" : "key"} onChange={(e) => setLeadOnly(e.target.value === "lead")}><option value="key">Intro + key sections</option><option value="lead">Intro only</option></select></Field>
        </div>
      )}
      {!active && <div className="flex flex-wrap items-center gap-3"><Button variant="primary" icon="play" disabled={!zim} onClick={() => void start()}>{p && p.state !== "idle" && p.zim === zim ? "Start / continue" : "Start indexing"}</Button><Help>Safe to stop at any time; it resumes where it left off.</Help></div>}

      {p && p.state !== "idle" && (
        <div className="mt-4" role="status">
          <div className="mb-1 flex flex-wrap items-center gap-2 text-sm">
            <Badge tone={p.state === "done" ? "teal" : p.state === "error" ? "danger" : "amber"}>{p.state}</Badge>
            <span className="font-mono text-xs text-muted">{p.zim}</span>
            {active && <span className="ml-auto flex gap-1">
              {p.state === "running" ? <Button variant="ghost" icon="pause" onClick={() => void control("pause")}>Pause</Button> : <Button variant="ghost" icon="play" onClick={() => void control("resume")}>Resume</Button>}
              <Button variant="ghost" icon="stop" onClick={() => void control("stop")}>Stop</Button></span>}
          </div>
          <Progress value={p.state === "done" ? 100 : pct} label="Indexing progress" tone={p.state === "done" ? "teal" : "amber"} />
          <p className="mt-1 font-mono text-xs text-muted">
            {p.articles.toLocaleString()} articles · {p.chunks.toLocaleString()} chunks · {p.entries_done.toLocaleString()}/{p.entries_total.toLocaleString()} entries
            {active && p.articles_per_s > 0 && ` · ${p.articles_per_s}/s · about ${fmtDuration(p.eta_s)} left`}
          </p>
          {p.error && <p className="mt-2 text-sm text-danger" role="alert">{p.error}</p>}
        </div>
      )}
    </Card>
  );
}
