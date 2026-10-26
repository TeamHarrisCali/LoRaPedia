import { useState } from "react";
import { Link } from "react-router-dom";
import { QueryItem } from "../components/QueryItem";
import { Sparkline } from "../components/Sparkline";
import { SignalGlyph } from "../components/SignalGlyph";
import { Button, Card, EmptyState, PageHeader, SectionTitle } from "../components/ui";
import { api, errorText } from "../lib/api";
import { greeting } from "../lib/format";
import { useSettings } from "../lib/settings";
import { useToast } from "../lib/toast";
import type { Health, KbStats, RadioStatus, WizardState } from "../lib/types";
import { useTestQuestion } from "../lib/useTestQuestion";
import { useQueries } from "../lib/useQueries";
import { usePoll } from "../lib/usePoll";

function StatCard({ title, value, sub, to }: { title: string; value: React.ReactNode; sub: React.ReactNode; to: string }) {
  return (
    <Link to={to} className="block rounded-xl border border-line bg-surface/90 p-4 transition hover:border-muted">
      <p className="text-xs font-medium uppercase tracking-wide text-muted">{title}</p>
      <p className="mt-1 truncate font-serif text-2xl font-semibold">{value}</p>
      <p className="mt-0.5 truncate text-sm text-muted">{sub}</p>
    </Link>
  );
}

export function Dashboard() {
  const toast = useToast();
  const { settings } = useSettings();
  const health = usePoll(() => api.get<Health>("/api/health"), 3000);
  const radio = usePoll(() => api.get<RadioStatus>("/api/radio/status"), 5000);
  const llm = usePoll(() => api.get<{ ok: boolean; detail: string }>("/api/llm/status"), 10000);
  const kb = usePoll(() => api.get<KbStats>("/api/kb/stats"), 10000);
  const act = usePoll(() => api.get<{ hours: number[]; queries: number; avg_latency_s: number }>("/api/stats/activity"), 15000);
  const wizard = usePoll(() => api.get<WizardState>("/api/wizard"), 20000);
  const { queries, loaded, reload } = useQueries(5);

  const [text, setText] = useState("");
  const { ask, sending, answer, note } = useTestQuestion(() => void act.reload());

  async function seed() {
    try { await api.post("/api/demo/seed"); toast.success("Added demo conversations."); void reload(); void act.reload(); }
    catch (e) { toast.error(errorText(e)); }
  }

  const h = health.data, r = radio.data;
  const unreachable = !!health.error && !h; // only after a request actually failed, not while loading
  return (
    <>
      <PageHeader title={`${greeting()}.`} subtitle={unreachable ? "Cannot reach the LoRaPedia server. Is it running?" : h ? `Your node is online${r?.active === "simulator" ? " in simulator mode: no radio attached" : ""}.` : "Connecting…"} />

      {wizard.data && !wizard.data.finished && (
        <Card className="mb-5 flex flex-wrap items-center justify-between gap-3 border-amber/50">
          <div><p className="font-serif text-lg font-semibold">Finish setting up</p><p className="text-sm text-muted">{wizard.data.done.length} of 6 steps done. It takes a few minutes.</p></div>
          <Link to="/setup" className="rounded-lg bg-amber px-4 py-2 text-sm font-semibold text-amber-on">Continue setup</Link>
        </Card>
      )}

      <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard title="Radio" to="/radio"
          value={r?.active === "meshtastic" ? (r.device.short_name ?? r.node_id) : "Simulator"}
          sub={r?.active === "meshtastic" ? <span className="inline-flex items-center gap-1.5"><SignalGlyph snr={r.snr} />{r.rssi != null ? `${r.rssi} dBm` : "waiting for packets"}</span> : (r?.error ?? "no device")} />
        <StatCard title="LLM" to="/model" value={settings?.llm_backend === "mock" ? "Demo mode" : (settings?.llm_model ?? "–")}
          sub={llm.data ? (llm.data.ok ? "ready" : llm.data.detail) : "checking…"} />
        <StatCard title="Wikipedia" to="/knowledge" value={kb.data && kb.data.articles > 0 ? kb.data.articles.toLocaleString() : "Demo data"}
          sub={kb.data && kb.data.articles > 0 ? "articles indexed" : "add a ZIM to expand"} />
        <StatCard title="Today" to="/feed" value={`${act.data?.queries ?? 0} Qs`} sub={`${(act.data?.avg_latency_s ?? 0).toFixed(1)}s average`} />
      </div>

      <Card className="mb-5">
        <SectionTitle hint="Questions answered per hour">Activity (24h)</SectionTitle>
        <Sparkline data={act.data?.hours ?? new Array(24).fill(0)} label={`Questions per hour over the last 24 hours, ${act.data?.hours.reduce((a, b) => a + b, 0) ?? 0} in total`} />
        <div className="mt-1 flex justify-between text-xs text-muted"><span>24h ago</span><span>now</span></div>
      </Card>

      <Card className="mb-5">
        <SectionTitle hint="Pretends to be a node on the mesh and goes through the whole pipeline. Nothing is sent over the air.">Send test question</SectionTitle>
        <form onSubmit={(e) => { e.preventDefault(); void ask(text); }} className="flex flex-wrap gap-2">
          <label className="sr-only" htmlFor="test-q">Test question</label>
          <input id="test-q" className="field min-w-56 flex-1" placeholder="e.g. what is a quasar?" value={text} onChange={(e) => setText(e.target.value)} />
          <Button type="submit" variant="primary" icon="send" busy={sending}>Ask</Button>
        </form>
        {note && <p className="mt-3 text-sm text-amber-text" role="status">{note}</p>}
        {sending && <p className="mt-3 text-sm text-muted" role="status">Thinking… the first answer after startup can take a while while the model loads.</p>}
        {answer && <ul className="mt-4"><QueryItem q={answer} /></ul>}
      </Card>

      <SectionTitle>Recent</SectionTitle>
      {loaded && queries.length === 0 ? (
        <EmptyState title="No questions yet">
          <p>Ask one above, or load some sample conversations to see how the dashboard looks.</p>
          <Button className="mt-3" onClick={seed}>Load demo data</Button>
        </EmptyState>
      ) : <ul className="space-y-3">{queries.map((q) => <QueryItem key={q.id} q={q} />)}</ul>}
    </>
  );
}
