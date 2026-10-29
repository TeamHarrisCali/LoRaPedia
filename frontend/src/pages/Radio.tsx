import { useState } from "react";
import { Link } from "react-router-dom";
import { Bubble } from "../components/Bubble";
import { ByteMeter } from "../components/ByteMeter";
import { SaveBar } from "../components/SaveBar";
import { TabPanel, Tabs, rememberTab, rememberedTab } from "../components/Tabs";
import { Card, Dot, Field, PageHeader, SectionTitle, Toggle } from "../components/ui";
import { api } from "../lib/api";
import { utf8Bytes } from "../lib/format";
import { useLive } from "../lib/live";
import { useDraft } from "../lib/settings";
import type { RadioStatus } from "../lib/types";
import { usePoll } from "../lib/usePoll";
import { RadioConnect } from "../components/RadioConnect";

const SAMPLE = "Photosynthesis converts light, water and carbon dioxide into sugar and oxygen. [wp:Photosynthesis]";

const TABS = [{ id: "connection", label: "Connection" }, { id: "replies", label: "Replies" }];

export function Radio() {
  const { draft, set, dirty, save, discard } = useDraft();
  const live = useLive();
  const status = usePoll(() => api.get<RadioStatus>("/api/radio/status"), 4000);
  const st = live.radio ?? status.data;
  const [prefixText, setPrefixText] = useState<string | null>(null);
  const [tab, setTab] = useState(() => rememberedTab("radio", "connection", TABS.map((t) => t.id)));
  if (!draft) return <p className="text-muted" role="status">Loading…</p>;

  const prefixes = prefixText ?? draft.trigger_prefixes.join(", ");
  const sampleBytes = utf8Bytes(SAMPLE);
  const usable = (n: number) => n - 6; // room left per part once "(2/3) " is added

  return (
    <>
      <PageHeader title="Radio connection" subtitle="How LoRaPedia talks to your Meshtastic device, and how long replies can be. Who may ask is on the Limits page." />

      <Tabs label="Radio sections" tabs={TABS} active={tab} onChange={(id) => { setTab(id); rememberTab("radio", id); }} />

      <TabPanel id="connection" active={tab}>
      <Card className="mb-5">
        <SectionTitle hint="Plug the radio in over USB and leave this on Auto. Or choose another connection.">Connection</SectionTitle>
        <RadioConnect status={st} onChanged={() => { void status.reload(); }} />
      </Card>

      </TabPanel>

      <TabPanel id="replies" active={tab}>
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
        <SectionTitle hint="Who may ask, and how much, is on its own page now.">Blocking, allowing and daily limits</SectionTitle>
        <p className="text-sm">
          {draft.blocklist.length > 0 || draft.allowlist.length > 0
            ? <><Dot tone="amber" label="Active" className="mr-2" />{draft.blocklist.length} blocked · {draft.allowlist.length > 0 ? `${draft.allowlist.length} allowed (only these are answered)` : "everyone allowed"}. </>
            : "Everyone may ask. "}
          Block or allow a node, and set how many questions each node may ask per day, on the <Link className="underline" to="/limits">Limits page</Link>.
        </p>
      </Card>
      </TabPanel>
      <SaveBar dirty={dirty} onSave={() => void save()} onDiscard={() => { discard(); setPrefixText(null); }} />
    </>
  );
}

