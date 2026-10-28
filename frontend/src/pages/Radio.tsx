import { useMemo, useState } from "react";
import { Pager } from "../components/Pager";
import { usePaged } from "../lib/usePaged";
import { Bubble } from "../components/Bubble";
import { ByteMeter } from "../components/ByteMeter";
import { SaveBar } from "../components/SaveBar";
import { SignalGlyph } from "../components/SignalGlyph";
import { Badge, Card, Dot, EmptyState, Field, PageHeader, SectionTitle, Toggle } from "../components/ui";
import { api, errorText } from "../lib/api";
import { timeAgo, utf8Bytes } from "../lib/format";
import { useLive } from "../lib/live";
import { useDraft, useSettings } from "../lib/settings";
import { useToast } from "../lib/toast";
import type { RadioNode, RadioStatus } from "../lib/types";
import { usePoll } from "../lib/usePoll";
import { RadioConnect } from "../components/RadioConnect";

const SAMPLE = "Photosynthesis converts light, water and carbon dioxide into sugar and oxygen. [wp:Photosynthesis]";

export function Radio() {
  const toast = useToast();
  const { reload } = useSettings();
  const { draft, set, dirty, save, discard } = useDraft();
  const live = useLive();
  const status = usePoll(() => api.get<RadioStatus>("/api/radio/status"), 4000);
  const nodes = usePoll(() => api.get<RadioNode[]>("/api/radio/nodes"), 8000);
  const st = live.radio ?? status.data;
  const [prefixText, setPrefixText] = useState<string | null>(null);
  const [nodeQuery, setNodeQuery] = useState("");
  const foundNodes = useMemo(() => {
    const words = nodeQuery.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return (nodes.data ?? []).filter((n) => words.every((w) => `${n.short} ${n.long} ${n.id}`.toLowerCase().includes(w)));
  }, [nodes.data, nodeQuery]);
  const nodePage = usePaged(foundNodes, 12, nodeQuery);
  if (!draft) return <p className="text-muted" role="status">Loading…</p>;

  async function toggleNode(n: RadioNode, which: "block" | "allow", on: boolean) {
    try { await api.post(`/api/radio/nodes/${encodeURIComponent(n.id)}/${which}`, { on }); await reload(); await nodes.reload(); }
    catch (e) { toast.error(errorText(e)); }
  }
  const prefixes = prefixText ?? draft.trigger_prefixes.join(", ");
  const sampleBytes = utf8Bytes(SAMPLE);
  const usable = (n: number) => n - 6; // room left per part once "(2/3) " is added

  return (
    <>
      <PageHeader title="Radio" subtitle="How LoRaPedia talks to your Meshtastic device, who it answers, and how long replies can be." />

      <Card className="mb-5">
        <SectionTitle hint="Plug the radio in over USB and leave this on Auto. Or choose another connection.">Connection</SectionTitle>
        <RadioConnect status={st} onChanged={() => { void status.reload(); void nodes.reload(); }} />
      </Card>

      <Card className="mb-5">
        <SectionTitle hint="Which messages the node answers.">Trigger</SectionTitle>
        <p className="mb-4 rounded-lg border border-line bg-bg p-3 text-sm">
          <b>Direct messages only.</b> Anyone can message this node directly and gets a private answer. It never replies in a public channel,
          and anything said in a channel is ignored. This is not a setting: it cannot be switched on by mistake.
        </p>
        <div className="grid gap-x-6 sm:grid-cols-2">
          <Field label="Trigger prefixes" htmlFor="prefixes" help="Separate with commas. Optional in a direct message: &quot;?what is a quasar&quot; and &quot;what is a quasar&quot; both work.">
            <input id="prefixes" className="field font-mono" value={prefixes}
              onChange={(e) => { setPrefixText(e.target.value); set({ trigger_prefixes: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) }); }} />
          </Field>
          <Field label={`Hop limit: ${draft.hop_limit}`} htmlFor="hops" help="How many relays a reply may pass through. Higher reaches farther but uses more airtime for everyone.">
            <input id="hops" type="range" min={1} max={7} value={draft.hop_limit} onChange={(e) => set({ hop_limit: Number(e.target.value) })} />
          </Field>
        </div>
      </Card>

      <Card className="mb-5">
        <SectionTitle hint="Meshtastic text messages are tiny. LoRaPedia counts bytes, not characters, so accents and symbols cost more.">Reply size</SectionTitle>
        <Field label={`Byte limit per message: ${draft.byte_limit} B`} htmlFor="limit" help="Meshtastic's hard maximum is 233 bytes. 200 leaves a safety margin. About one English character per byte.">
          <input id="limit" type="range" min={50} max={233} value={draft.byte_limit} onChange={(e) => set({ byte_limit: Number(e.target.value) })} />
        </Field>
        <div className="mb-5 space-y-2 rounded-lg border border-line bg-bg p-3">
          <p className="text-xs text-muted">Preview: a typical short answer</p>
          <Bubble side="out" label="Sample answer">{SAMPLE}</Bubble>
          <ByteMeter bytes={sampleBytes} limit={draft.byte_limit} />
        </div>
        <Toggle checked={draft.multipart_enabled} onChange={(v) => set({ multipart_enabled: v })} label="Send long answers in several messages"
          help={`When an answer will not fit in one message, split it into numbered parts like (1/3). Each part uses up to ${usable(draft.byte_limit)} bytes of text.`} />
        {draft.multipart_enabled && (
          <div className="mb-2 ml-8 grid gap-x-6 sm:grid-cols-2">
            <Field label={`Most parts per answer: ${draft.max_parts}`} htmlFor="parts" help={`Caps airtime. At most ~${draft.max_parts * usable(draft.byte_limit)} bytes of answer in total.`}>
              <input id="parts" type="range" min={2} max={9} value={draft.max_parts} onChange={(e) => set({ max_parts: Number(e.target.value) })} />
            </Field>
            <Toggle checked={draft.auto_send_parts} onChange={(v) => set({ auto_send_parts: v })} label="Send all parts automatically"
              help="Off: send part 1 and let the person reply ?more for the rest. Better on busy meshes." />
          </div>
        )}
        <Field label={`Pause between messages: ${draft.send_delay_s}s`} htmlFor="delay" help="Gives other nodes airtime. LoRa is slow and shared; 5s or more is polite.">
          <input id="delay" type="range" min={0} max={30} step={1} value={draft.send_delay_s} onChange={(e) => set({ send_delay_s: Number(e.target.value) })} />
        </Field>
      </Card>

      <Card className="mb-5">
        <SectionTitle hint="Nodes this radio has heard. Block a node to ignore it. If anything is on the allow list, only allowed nodes are answered.">Nodes</SectionTitle>
        {!nodes.data || nodes.data.length === 0 ? (
          <EmptyState title="No nodes yet">{st?.active === "simulator" ? "Nodes appear once a real radio is connected." : "Nodes show up as the radio hears them."}</EmptyState>
        ) : (
          <>
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <input aria-label="Search nodes" type="search" className="field max-w-xs" placeholder="Search name or id…" value={nodeQuery} onChange={(e) => setNodeQuery(e.target.value)} />
            <span className="text-sm text-muted">{foundNodes.length} of {nodes.data.length}</span>
          </div>
          {foundNodes.length === 0 && <p className="text-sm text-muted">No node matches.</p>}
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Nodes heard by this radio</caption>
              <thead className="text-xs uppercase tracking-wide text-muted"><tr>
                <th className="py-2 pr-3 font-medium">Node</th><th className="pr-3 font-medium">ID</th><th className="pr-3 font-medium">Heard</th><th className="pr-3 font-medium">Hops</th><th className="pr-3 font-medium">Block</th><th className="font-medium">Allow</th>
              </tr></thead>
              <tbody>
                {nodePage.slice.map((n) => (
                  <tr key={n.id} className="border-t border-line">
                    <td className="py-2 pr-3"><span className="flex items-center gap-2"><SignalGlyph snr={n.snr} /><span><span className="font-semibold">{n.short}</span>{n.is_self && <> <Badge tone="teal">this node</Badge></>}<span className="block text-xs text-muted">{n.long}</span></span></span></td>
                    <td className="pr-3 font-mono text-xs">{n.id}</td>
                    <td className="pr-3 text-muted">{timeAgo(n.last_heard)}</td>
                    <td className="pr-3 font-mono">{n.hops_away ?? "–"}</td>
                    <td className="pr-3"><input type="checkbox" role="switch" aria-label={`Block ${n.short}`} disabled={n.is_self} checked={n.blocked} onChange={(e) => toggleNode(n, "block", e.target.checked)} className="h-5 w-5" /></td>
                    <td><input type="checkbox" role="switch" aria-label={`Allow ${n.short}`} disabled={n.is_self} checked={n.allowed} onChange={(e) => toggleNode(n, "allow", e.target.checked)} className="h-5 w-5" /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager page={nodePage.page} pages={nodePage.pages} total={nodePage.total} size={nodePage.size} onPage={nodePage.setPage} noun="nodes" />
          </>
        )}
        {(draft.blocklist.length > 0 || draft.allowlist.length > 0) && (
          <p className="mt-3 text-sm text-muted"><Dot tone="amber" label="Active" className="mr-2" />Blocked: <span className="font-mono">{draft.blocklist.join(", ") || "none"}</span> · Allowed: <span className="font-mono">{draft.allowlist.join(", ") || "everyone"}</span></p>
        )}
      </Card>
      <SaveBar dirty={dirty} onSave={() => void save()} onDiscard={() => { discard(); setPrefixText(null); }} />
    </>
  );
}

