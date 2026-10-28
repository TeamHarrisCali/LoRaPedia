import { api } from "../lib/api";
import { fmtBytes, timeAgo } from "../lib/format";
import type { MeshData, Settings } from "../lib/types";
import { usePoll } from "../lib/usePoll";
import { Card, Field, SectionTitle, Toggle } from "./ui";

const NAMES: Record<string, string> = {
  nodes: "Nodes", telemetry: "Sensor and battery readings", positions: "Position trail points", packets: "Packet counts by hour", links: "Signal by hour", hops: "Hop counts by hour",
};

/** Settings -> Mesh data: what the radio hears of other nodes is kept here, and for how long. */
export function MeshDataCard({ draft, set }: { draft: Settings; set: (p: Partial<Settings>) => void }) {
  const data = usePoll(() => api.get<MeshData>("/api/mesh/data"), 30000);
  const d = data.data;
  return (
    <Card className="mb-5">
      <SectionTitle hint="The radio hears other nodes' names, positions and sensor readings. LoRaPedia can keep them to build the Nodes page. They stay on this computer, and the content of messages is never kept.">Mesh data</SectionTitle>
      <Toggle checked={draft.mesh_record} onChange={(v) => set({ mesh_record: v })} label="Remember the nodes the radio hears"
        help="Names, hardware, last position, battery and sensor readings, signal strength and hop counts. Turn this off to stop recording; what is already kept stays until it expires or you delete the file." />
      <div className="max-w-xs">
        <Field label="Days of history to keep" htmlFor="mesh-days" help="Readings, signal and packet counts older than this are deleted. Position trails are kept three times as long. A node not heard for 90 days is forgotten.">
          <input id="mesh-days" type="number" min={1} max={365} className="field" value={draft.mesh_retention_days} onChange={(e) => set({ mesh_retention_days: Number(e.target.value) })} />
        </Field>
      </div>
      {d && (
        <dl className="mt-2 grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[14rem_1fr]" aria-label="What is kept">
          {Object.entries(d.datasets).filter(([k]) => NAMES[k]).map(([k, v]) => (
            <div key={k} className="contents"><dt className="text-muted">{NAMES[k]}</dt><dd>{v.rows.toLocaleString()}{k === "nodes" && v.last ? `, latest heard ${timeAgo(v.last)}` : ""}</dd></div>
          ))}
          <div className="contents"><dt className="text-muted">File size</dt><dd>{fmtBytes(d.size)} (<span className="font-mono">mesh.db</span> in the data folder)</dd></div>
        </dl>
      )}
    </Card>
  );
}
