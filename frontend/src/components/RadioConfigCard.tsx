import type { Settings } from "../lib/types";
import { Card, SectionTitle, Toggle } from "./ui";

/** Settings -> Radio settings editor: the switch that lets the Radio settings page change the radio. Looking is always allowed. */
export function RadioConfigCard({ draft, set }: { draft: Settings; set: (p: Partial<Settings>) => void }) {
  return (
    <Card className="mb-5">
      <SectionTitle hint="The Radio settings page shows what is stored on the radio itself. Changing it is a separate switch, because a wrong value can cut the radio off from your mesh or be illegal where you are.">Radio settings editor</SectionTitle>
      <Toggle checked={draft.operator_radio_config} onChange={(v) => set({ operator_radio_config: v })} label="Allow me to change the radio's settings from the Radio settings page"
        help="Off by default. When on, you can change the region, role, name, position and other settings after a confirmation that lists what changes and what could go wrong. A copy of the settings is kept before every change so you can go back, and Wi-Fi passwords, keys and MQTT credentials are never shown or touched. The web interface has no password, so anyone who can open it could change the radio while this is on: leave it off unless only you can reach this page." />
    </Card>
  );
}
