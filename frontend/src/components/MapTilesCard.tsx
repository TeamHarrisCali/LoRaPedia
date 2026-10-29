import { api, errorText } from "../lib/api";
import { fmtBytes } from "../lib/format";
import { useToast } from "../lib/toast";
import { usePoll } from "../lib/usePoll";
import type { Settings } from "../lib/types";
import { Button, Card, SectionTitle, Toggle } from "./ui";

export const MAP_TILES_NOTE = "To draw it, LoRaPedia downloads small map pictures (tiles) from OpenStreetMap for the area you look at, one time each, and keeps them on this computer so those places keep working offline. OpenStreetMap can see this computer's internet address and which places you looked at. Nothing about your nodes, their names or their positions is ever sent.";

/** Settings -> Mesh and radio -> Street map: off by default, because LoRaPedia otherwise never contacts anyone but the update check. */
export function MapTilesCard({ draft, set }: { draft: Settings; set: (p: Partial<Settings>) => void }) {
  const toast = useToast();
  const info = usePoll(() => api.get<{ tiles: number; bytes: number; limit_bytes: number }>("/api/map/tiles"), 30000);
  async function clear() {
    try { const r = await api.del<{ removed: number }>("/api/map/tiles"); toast.success(`Removed ${r.removed} saved map pictures.`); void info.reload(); }
    catch (e) { toast.error(errorText(e)); }
  }
  const i = info.data;
  return (
    <Card className="mb-5">
      <SectionTitle hint="The Map page draws a plain grid, which works without the internet. A street map shows roads, towns and terrain behind the nodes.">Street map</SectionTitle>
      <Toggle checked={draft.map_tiles} onChange={(v) => set({ map_tiles: v })} label="Show a street map (OpenStreetMap) on the Map page" help={MAP_TILES_NOTE} />
      <p className="mb-3 text-sm text-muted">Map data © <a className="underline" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a>. Pictures are fetched only for what you are looking at, never in bulk, and kept for a month.</p>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="text-muted">Saved here: {i ? `${i.tiles.toLocaleString()} pictures, ${fmtBytes(i.bytes)} (up to ${fmtBytes(i.limit_bytes)} are kept)` : "…"}</span>
        <Button onClick={clear} disabled={!i || i.tiles === 0}>Delete saved pictures</Button>
      </div>
    </Card>
  );
}
