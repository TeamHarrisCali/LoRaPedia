import { api, errorText } from "../lib/api";
import type { Settings } from "../lib/types";
import { useToast } from "../lib/toast";
import { usePoll } from "../lib/usePoll";
import { Button, Card, Field, SectionTitle, Toggle } from "./ui";

/** Settings -> Mesh AI: questions about the mesh itself, and how long each person's conversation is remembered. */
export function MeshAiCard({ draft, set }: { draft: Settings; set: (p: Partial<Settings>) => void }) {
  const toast = useToast();
  const memory = usePoll(() => api.get<{ people: number; hours: number }>("/api/memory"), 15000);
  const forget = async () => {
    try {
      const r = await api.post<{ cleared: number }>("/api/memory/clear");
      toast.success(r.cleared ? `Forgot ${r.cleared} conversation${r.cleared === 1 ? "" : "s"}.` : "There was nothing to forget.");
      void memory.reload();
    } catch (e) { toast.error(errorText(e)); }
  };
  return (
    <Card className="mb-5">
      <SectionTitle hint="What LoRaPedia can say about the mesh around it, and how it handles follow-up questions. Neither sends anything on its own: it only ever replies to the direct message it was asked in.">Mesh AI</SectionTitle>
      <Toggle checked={draft.mesh_questions} onChange={(v) => set({ mesh_questions: v })} label="Answer questions about the mesh"
        help="“How many nodes are around?”, “nearest router”, “which node has the lowest battery?”, “temperature outside”, “tell me about node …”, “what time is it?”. Answered from what the radio has heard, by fixed lookups: no language model is involved, so a node name can never act as an instruction, and what is not known is said so. Replies use a node's own name, never the label or note you gave it. Turn this off to send every question to Wikipedia." />
      <div className="max-w-xs">
        <Field label="Remember a conversation for (hours)" htmlFor="mem-hours" help="So “and its population?” works after “what is the capital of Japan?”. Kept in memory only (never on disk, never in a backup), separately for each person, and forgotten after this long without a question, when they send ?reset, or when the app restarts. 0 turns it off.">
          <input id="mem-hours" type="number" min={0} max={168} className="field" value={draft.memory_hours} onChange={(e) => set({ memory_hours: Number(e.target.value) })} />
        </Field>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="text-muted">{memory.data ? `Remembering ${memory.data.people} ${memory.data.people === 1 ? "person" : "people"} right now.` : "…"}</span>
        <Button onClick={forget} disabled={!memory.data?.people}>Forget everyone now</Button>
      </div>
    </Card>
  );
}
