import { useState } from "react";
import { Bubble } from "../components/Bubble";
import { ByteMeter } from "../components/ByteMeter";
import { ModelInstaller } from "../components/ModelInstaller";
import { SaveBar } from "../components/SaveBar";
import { Badge, Button, Card, Dot, Field, Help, PageHeader, SectionTitle, Spinner } from "../components/ui";
import { api, errorText } from "../lib/api";
import { useDraft, useSettings } from "../lib/settings";
import { useToast } from "../lib/toast";
import type { KbStats, TryResult } from "../lib/types";
import { usePoll } from "../lib/usePoll";

export function Model() {
  const toast = useToast();
  const { defaults } = useSettings();
  const { draft, set, dirty, save, discard } = useDraft();
  const llm = usePoll(() => api.get<{ ok: boolean; detail: string }>("/api/llm/status"), 8000);
  const kb = usePoll(() => api.get<KbStats>("/api/kb/stats"), 15000);
  const [q, setQ] = useState("what is a quasar?");
  const [trying, setTrying] = useState(false);
  const [result, setResult] = useState<TryResult | null>(null);
  if (!draft) return <p className="text-muted" role="status">Loading…</p>;

  async function tryIt() {
    if (!draft) return;
    setTrying(true); setResult(null);
    try {
      setResult(await api.post<TryResult>("/api/model/try", { question: q, system_prompt: draft.system_prompt, temperature: draft.temperature, max_tokens: draft.max_tokens }));
    } catch (e) { toast.error(errorText(e)); } finally { setTrying(false); }
  }

  const demo = draft.llm_backend === "mock";
  const embedderMismatch = kb.data?.embedder && kb.data.articles > 0 &&
    kb.data.embedder !== (draft.embed_backend === "hash" ? "hash-384" : `ollama:${draft.embed_model}`);

  return (
    <>
      <PageHeader title="Model" subtitle="The language model that writes answers from Wikipedia text, and the embedding model that finds the right text. Small models are fine here: answers are only a couple of hundred bytes." />

      <Card className="mb-5">
        <SectionTitle hint="Runs on this machine through Ollama (or any OpenAI-compatible server). No cloud.">Language model</SectionTitle>
        <p className="mb-3 flex items-center gap-2 text-sm" role="status">
          <Dot tone={llm.data?.ok ? "teal" : llm.data ? "danger" : "muted"} label={llm.data?.ok ? "Ready" : "Not ready"} />
          {llm.data ? (llm.data.ok ? <>Ready: <span className="font-mono">{llm.data.detail}</span></> : <span className="text-danger">{llm.data.detail}</span>) : "Checking…"}
        </p>
        <label className="mb-4 flex cursor-pointer items-start gap-3">
          <input type="checkbox" role="switch" className="mt-1 h-5 w-5" checked={demo} onChange={(e) => set({ llm_backend: e.target.checked ? "mock" : "openai" })} />
          <span><span className="text-sm font-medium">Demo mode (no model)</span><Help>Answers by quoting the top Wikipedia passage. Useful to try the system before installing a model.</Help></span>
        </label>
        {!demo && <>
          <ModelInstaller kind="llm" current={draft.llm_model} onChoose={(t) => set({ llm_model: t })} />
          <details className="mt-4">
            <summary className="cursor-pointer text-sm text-muted">Advanced: server address</summary>
            <Field label="LLM server URL" htmlFor="llm-url" help="Ollama default is http://localhost:11434/v1. llama.cpp and LM Studio use the same style of address.">
              <input id="llm-url" className="field font-mono" value={draft.llm_base_url} onChange={(e) => set({ llm_base_url: e.target.value })} />
            </Field>
            <Field label="Model name" htmlFor="llm-model"><input id="llm-model" className="field font-mono" value={draft.llm_model} onChange={(e) => set({ llm_model: e.target.value })} /></Field>
          </details>
        </>}
      </Card>

      <Card className="mb-5">
        <SectionTitle hint="Turns text into numbers so searches match by meaning, not just keywords.">Embedding model</SectionTitle>
        <label className="mb-4 flex cursor-pointer items-start gap-3">
          <input type="checkbox" role="switch" className="mt-1 h-5 w-5" checked={draft.embed_backend === "hash"} onChange={(e) => set({ embed_backend: e.target.checked ? "hash" : "ollama" })} />
          <span><span className="text-sm font-medium">Keyword-only search (no embedding model)</span><Help>Works instantly with nothing to install, but finds fewer paraphrased questions. You can switch later.</Help></span>
        </label>
        {draft.embed_backend === "ollama" && <ModelInstaller kind="embed" current={draft.embed_model} onChoose={(t) => set({ embed_model: t })} />}
        {embedderMismatch && (
          <p className="mt-3 rounded-lg border border-amber/60 p-3 text-sm" role="alert">
            <Badge tone="amber">Re-index needed</Badge> Your index was built with <span className="font-mono">{kb.data?.embedder}</span>. Vector search stays off until you remove the library entry on the Knowledge page and index again; keyword search keeps working.
          </p>
        )}
      </Card>

      <Card className="mb-5">
        <SectionTitle hint="Lower temperature = more literal and repeatable. Facts want low.">Tuning</SectionTitle>
        <div className="grid gap-x-6 sm:grid-cols-3">
          <Field label={`Temperature: ${draft.temperature}`} htmlFor="temp"><input id="temp" type="range" min={0} max={1} step={0.05} value={draft.temperature} onChange={(e) => set({ temperature: Number(e.target.value) })} /></Field>
          <Field label={`Max tokens: ${draft.max_tokens}`} htmlFor="tokens" help="Upper bound on generation. 200 bytes is about 50 tokens; extra room lets multi-part answers finish."><input id="tokens" type="range" min={32} max={512} step={8} value={draft.max_tokens} onChange={(e) => set({ max_tokens: Number(e.target.value) })} /></Field>
          <Field label={`Passages retrieved: ${draft.top_k}`} htmlFor="topk" help="More context can help, but small models get confused by too much."><input id="topk" type="range" min={1} max={10} value={draft.top_k} onChange={(e) => set({ top_k: Number(e.target.value) })} /></Field>
        </div>
      </Card>

      <Card className="mb-5">
        <SectionTitle hint="The instructions every answer starts from. The defaults are tuned for short, factual, plain-text replies.">System prompt</SectionTitle>
        <label className="sr-only" htmlFor="sys">System prompt</label>
        <textarea id="sys" className="field min-h-32 font-mono text-sm" value={draft.system_prompt} onChange={(e) => set({ system_prompt: e.target.value })} />
        <Button className="mt-2" variant="ghost" disabled={!defaults || draft.system_prompt === defaults.system_prompt} onClick={() => defaults && set({ system_prompt: defaults.system_prompt })}>Reset to default</Button>
      </Card>

      <Card className="mb-5">
        <SectionTitle hint="Runs the full pipeline using the settings above, including unsaved edits. Nothing is sent over the radio or logged.">Try it</SectionTitle>
        <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); void tryIt(); }}>
          <label className="sr-only" htmlFor="try-q">Question</label>
          <input id="try-q" className="field min-w-56 flex-1" value={q} onChange={(e) => setQ(e.target.value)} />
          <Button type="submit" variant="primary" icon="send" busy={trying}>Run</Button>
        </form>
        {trying && <p className="mt-3 flex items-center gap-2 text-sm text-muted" role="status"><Spinner /> Working…</p>}
        {result && (
          <div className="mt-4 space-y-3">
            {result.parts.map((p, i) => (
              <div key={i} className="space-y-1.5"><Bubble side="out" label={`Answer ${result.parts.length > 1 ? `part ${i + 1}` : ""}`}>{p}</Bubble><ByteMeter bytes={result.bytes[i]} limit={result.byte_limit} /></div>
            ))}
            <p className="text-sm text-muted">
              {result.latency_s.toFixed(1)}s · {result.chunks.length ? `from ${[...new Set(result.chunks.map((c) => c.title))].join(", ")}` : "nothing relevant found"}{result.compressed && " · compressed once to fit"}
            </p>
            {result.raw && result.raw !== result.parts.join(" ") && <details><summary className="cursor-pointer text-sm text-muted">Raw model output before fitting</summary><p className="mt-1 whitespace-pre-wrap rounded-lg border border-line bg-bg p-3 font-mono text-xs">{result.raw}</p></details>}
          </div>
        )}
      </Card>
      <SaveBar dirty={dirty} onSave={() => void save()} onDiscard={discard} />
    </>
  );
}
