import { DELIVERY } from "../lib/delivery";
import type { DeliveryPart, DeliveryState } from "../lib/types";
import { Badge } from "./ui";

/** Whether a message arrived. The words carry the meaning; the colour only backs them up. */
export function DeliveryBadge({ state, detail, parts }: { state: DeliveryState | null | undefined; detail?: string | null; parts?: DeliveryPart[] }) {
  if (!state) return null;
  const d = DELIVERY[state];
  const why = state === "failed" ? (detail ?? parts?.find((p) => p.state === "failed")?.detail) : null;
  const some = parts && parts.length > 1 && state !== "delivered" ? ` (${parts.filter((p) => p.state === "delivered").length} of ${parts.length} confirmed)` : "";
  return <span title={`${d.help}${why ? ` Reason: ${why}.` : ""}`}><Badge tone={d.tone}>{d.label}{why ? `: ${why}` : ""}{some}</Badge></span>;
}
