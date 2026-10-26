import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { IndexControls } from "../components/IndexControls";
import { ModelInstaller } from "../components/ModelInstaller";
import { QueryItem } from "../components/QueryItem";
import { RadioConnect } from "../components/RadioConnect";
import { ZimLibrary } from "../components/ZimLibrary";
import { Badge, Button, Card, Check, Help, PageHeader } from "../components/ui";
import { api, errorText } from "../lib/api";
import { useLive } from "../lib/live";
import { useSettings } from "../lib/settings";
import { useToast } from "../lib/toast";
import type { HardwareInfo, KbStats, RadioStatus, WizardState, Zim } from "../lib/types";
import { useTestQuestion } from "../lib/useTestQuestion";
import { usePoll } from "../lib/usePoll";

const STEPS = [
  { key: "hardware", title: "Check this computer", blurb: "See what hardware you have." },
  { key: "llm", title: "Pick a language model", blurb: "The brain that writes answers." },
  { key: "radio", title: "Connect your radio", blurb: "Plug in your Meshtastic device." },
  { key: "wikipedia", title: "Add Wikipedia", blurb: "The offline library." },
  { key: "index", title: "Build the index", blurb: "Make it searchable." },
  { key: "test", title: "Test it", blurb: "Ask a question end to end." },
] as const;
type Key = (typeof STEPS)[number]["key"];

function HardwareStep() {
  const hw = usePoll(() => api.get<HardwareInfo>("/api/system/hardware"), 3_600_000);
  const h = hw.data?.hardware;
  if (!h) return <p className="text-muted" role="status">Looking at your hardware…</p>;
  const rows: [string, string][] = [
    ["Processor", `${h.cpu} (${h.cores} cores)`], ["Memory", `${h.ram_gb} GB`],
    ["Graphics card", h.gpu ? `${h.gpu.name}, ${h.gpu.vram_gb} GB` : "None found (that is fine)"],
    ["Free disk space", `${h.disk_free_gb} GB`], ...(h.board ? [["Board", h.board] as [string, string]] : []),
  ];
  return (
    <>
      <dl className="mb-4 grid gap-y-1 text-sm sm:grid-cols-[10rem_1fr]">{rows.map(([k, v]) => <div key={k} className="contents"><dt className="text-muted">{k}</dt><dd>{v}</dd></div>)}</dl>
      <p className="rounded-lg border border-line bg-bg p-3 text-sm">{hw.data?.suggestions.note} We will suggest a model that suits this machine in the next step.</p>
      {h.disk_free_gb < 5 && <p className="mt-3 text-sm text-danger" role="alert">Less than 5 GB free. The small Wikipedia file needs about 1 GB and a model about 2 GB.</p>}
    </>
  );
}

function ModelStep() {
  const { settings, save } = useSettings();
  const toast = useToast();
  if (!settings) return null;
  const patch = async (p: Partial<typeof settings>) => { if (await save({ ...settings, ...p })) toast.success("Saved."); };
  const demo = settings.llm_backend === "mock";
  return (
    <>
      <h3 className="mb-2 text-lg font-semibold">Language model</h3>
      {demo ? <p className="mb-3 rounded-lg border border-amber/60 p-3 text-sm">Demo mode is on: answers quote Wikipedia directly, no model needed. <button className="underline" onClick={() => void patch({ llm_backend: "openai" })}>Use a real model instead</button></p>
        : <ModelInstaller kind="llm" current={settings.llm_model} onChoose={(t) => void patch({ llm_model: t, llm_backend: "openai" })} />}
      <h3 className="mb-2 mt-6 text-lg font-semibold">Embedding model</h3>
      {settings.embed_backend === "hash" ? <p className="mb-3 rounded-lg border border-amber/60 p-3 text-sm">Keyword-only search is on. <button className="underline" onClick={() => void patch({ embed_backend: "ollama" })}>Use an embedding model instead</button></p>
        : <>
          <ModelInstaller kind="embed" current={settings.embed_model} onChoose={(t) => void patch({ embed_model: t })} />
          <p className="mt-3 text-sm text-muted">Do not want to download one? <button className="underline" onClick={() => void patch({ embed_backend: "hash" })}>Use keyword-only search</button>. It works instantly but finds fewer reworded questions.</p>
        </>}
      {!(demo && settings.embed_backend === "hash") && (
        <div className="mt-6 rounded-lg border border-line bg-bg p-3">
          <p className="mb-2 text-sm">No internet or in a hurry? Skip the downloads and try everything first.</p>
          <Button onClick={() => void patch({ llm_backend: "mock", embed_backend: "hash" })}>Use demo mode for now</Button>
          <Help>You can install real models later from the Model page.</Help>
        </div>
      )}
    </>
  );
}

function RadioStep() {
  const status = usePoll(() => api.get<RadioStatus>("/api/radio/status"), 3000);
  const live = useLive();
  return (
    <>
      <p className="mb-4 text-sm text-muted">Plug your Meshtastic radio into a USB port and press Retry. No radio yet? Use the simulator: you can type as a fake node and the rest works the same.</p>
      <RadioConnect status={live.radio ?? status.data} onChanged={() => void status.reload()} />
    </>
  );
}

function TestStep() {
  const [text, setText] = useState("what is a quasar?");
  const { ask, sending, answer, note } = useTestQuestion();
  return (
    <>
      <p className="mb-3 text-sm text-muted">This pretends to be a person on the mesh sending a question. The whole pipeline runs; nothing goes over the air.</p>
      <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); void ask(text); }}>
        <label className="sr-only" htmlFor="wz-q">Test question</label>
        <input id="wz-q" className="field min-w-56 flex-1" value={text} onChange={(e) => setText(e.target.value)} />
        <Button type="submit" variant="primary" icon="send" busy={sending}>Ask</Button>
      </form>
      {sending && <p className="mt-3 text-sm text-muted" role="status">Thinking… the first answer can take a while while the model loads.</p>}
      {note && <p className="mt-3 text-sm text-amber-text" role="status">{note}</p>}
      {answer && <ul className="mt-4"><QueryItem q={answer} /></ul>}
    </>
  );
}

export function Wizard() {
  const nav = useNavigate();
  const toast = useToast();
  const [state, setState] = useState<WizardState>({ done: [], skipped: [], finished: false, step: 0 });
  const [loaded, setLoaded] = useState(false);
  const kb = usePoll(() => api.get<KbStats>("/api/kb/stats"), 5000);
  const lib = usePoll(() => api.get<Zim[]>("/api/kb/library"), 4000);

  useEffect(() => { api.get<WizardState>("/api/wizard").then((w) => { setState(w); setLoaded(true); }).catch((e) => toast.error(errorText(e))); }, [toast]);

  async function update(patch: Partial<WizardState>) {
    const next = { ...state, ...patch };
    setState(next);
    try { await api.put("/api/wizard", next); } catch (e) { toast.error(errorText(e)); }
  }
  /** Move a step into `done` or `skipped`, removing it from the other list. */
  const mark = (key: Key, as: "done" | "skipped") => {
    const lists = { done: state.done.filter((k) => k !== key), skipped: state.skipped.filter((k) => k !== key) };
    lists[as] = [...lists[as], key];
    return lists;
  };
  const step = Math.min(state.step, STEPS.length - 1);
  useEffect(() => { window.scrollTo({ top: 0 }); }, [step]); // each step starts at the top
  const cur = STEPS[step];
  const last = step === STEPS.length - 1;

  async function advance(as: "done" | "skipped") {
    const lists = mark(cur.key, as);
    if (last) {
      await update({ ...lists, finished: as === "done" || state.finished, step });
      toast.success(as === "done" ? "Setup complete. Your node is ready." : "Setup closed. You can revisit any step from the Setup page.");
      nav("/");
    } else await update({ ...lists, step: step + 1 });
  }

  if (!loaded) return <p className="text-muted" role="status">Loading…</p>;
  const hasIndex = (kb.data?.articles ?? 0) > 0;
  // Steps with something verifiable only get a green check when it is true; otherwise the user can still skip.
  const blocker = cur.key === "wikipedia" && (lib.data?.length ?? 0) === 0 ? "Add a Wikipedia file above to continue, or skip this step."
    : cur.key === "index" && !hasIndex ? "Index at least some articles to continue, or skip this step." : null;
  return (
    <>
      <PageHeader title="Set up LoRaPedia" subtitle="Six short steps, about ten minutes. Every step can be skipped and revisited, and your progress is saved." />
      <div className="flex flex-col gap-5 lg:flex-row">
        <ol className="flex shrink-0 gap-2 overflow-x-auto lg:w-64 lg:flex-col" aria-label="Setup steps">
          {STEPS.map((s, i) => {
            const done = state.done.includes(s.key), skipped = state.skipped.includes(s.key);
            return (
              <li key={s.key}>
                <button onClick={() => void update({ step: i })} aria-current={i === step ? "step" : undefined}
                  className={`flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left ${i === step ? "border-amber bg-raised" : "border-line hover:bg-raised"}`}>
                  <Check done={done} />
                  <span className="min-w-0"><span className="block whitespace-nowrap text-sm font-semibold">{i + 1}. {s.title}</span>
                    <span className="hidden text-xs text-muted lg:block">{skipped ? "Skipped" : s.blurb}</span></span>
                </button>
              </li>
            );
          })}
        </ol>

        <Card className="min-w-0 flex-1">
          <h2 className="mb-4 flex flex-wrap items-center gap-3 text-2xl font-semibold">{cur.title}
            {state.skipped.includes(cur.key) && <Badge tone="amber">Skipped</Badge>}{state.done.includes(cur.key) && <Badge tone="teal">Done</Badge>}</h2>
          {cur.key === "hardware" && <HardwareStep />}
          {cur.key === "llm" && <ModelStep />}
          {cur.key === "radio" && <RadioStep />}
          {cur.key === "wikipedia" && <ZimLibrary onChanged={() => void kb.reload()} />}
          {cur.key === "index" && <>
            <p className="mb-3 text-sm text-muted">Tip: choose "First 10,000 articles" with "Intro only" for a working system in minutes. You can index the rest later.</p>
            <IndexControls onDone={() => void kb.reload()} />
            {hasIndex && <p className="mt-3 text-sm text-teal" role="status">{kb.data?.articles.toLocaleString()} articles are searchable now.</p>}
          </>}
          {cur.key === "test" && <TestStep />}

          <div className="mt-6 flex flex-wrap items-center gap-2 border-t border-line pt-4">
            <Button variant="ghost" disabled={step === 0} onClick={() => void update({ step: step - 1 })}>Back</Button>
            <span className="ml-auto flex gap-2">
              <Button variant="ghost" onClick={() => void advance("skipped")}>{last ? "Close for now" : "Skip for now"}</Button>
              <Button variant="primary" disabled={!!blocker} onClick={() => void advance("done")}>{last ? "Finish" : "Continue"}</Button>
            </span>
            {blocker && <p className="basis-full text-right text-sm text-muted" role="status">{blocker}</p>}
          </div>
        </Card>
      </div>
    </>
  );
}
