import type { Settings } from "../lib/types";
import { Card, Field, SectionTitle, Toggle } from "./ui";

/** Settings -> Messages: how the Messages page behaves. The AI is never involved in anything here. */
export function MessagingCard({ draft, set }: { draft: Settings; set: (p: Partial<Settings>) => void }) {
  return (
    <Card className="mb-5">
      <SectionTitle hint="The Messages page lets you write to a node yourself. LoRaPedia's AI never sends these, never sees them, and can never post to a public channel.">Messages</SectionTitle>
      <div className="max-w-xs">
        <Field label="Keep a chat open for (minutes)" htmlFor="chat-min"
          help="After you write to a node, what it sends back (unless it starts with ? or !wiki) is shown on the Messages page instead of being answered by the AI, so a friend's “see you there” does not get a Wikipedia article. Each reply keeps the chat going; “End chat” closes it at once. 0 turns this off: the AI answers every message, as before.">
          <input id="chat-min" type="number" min={0} max={1440} className="field" value={draft.operator_chat_minutes} onChange={(e) => set({ operator_chat_minutes: Number(e.target.value) })} />
        </Field>
      </div>
      <Toggle checked={draft.operator_public_post} onChange={(v) => set({ operator_public_post: v })} label="Allow me to post to a public channel from the Messages page"
        help="Off by default. When on, you can type a message and post it to a channel after a confirmation, at most once a minute, and only with a radio connected. A post is heard by everyone on the channel and relayed by every node that hears it. The web interface has no password, so anyone who can open it could post while this is on: leave it off unless only you can reach this page." />
    </Card>
  );
}
