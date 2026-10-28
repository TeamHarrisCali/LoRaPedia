import type { DeliveryState } from "./types";

/** Worst first: an answer only counts as delivered when every part was. (Same order as the server's.) */
const ORDER: DeliveryState[] = ["failed", "unconfirmed", "sent", "relayed", "delivered"];
export const worstDelivery = (states: DeliveryState[]): DeliveryState | null => ORDER.find((s) => states.includes(s)) ?? null;

export const DELIVERY: Record<DeliveryState, { label: string; tone: "muted" | "teal" | "amber" | "danger"; help: string }> = {
  delivered: { label: "Delivered", tone: "teal", help: "The other node confirmed it received the message." },
  relayed: { label: "Passed on", tone: "amber", help: "A neighbour was heard passing the message on, but the other node has not confirmed it yet. Often it did arrive." },
  sent: { label: "Sent, waiting", tone: "muted", help: "Sent. Waiting to hear whether anyone passed it on." },
  unconfirmed: { label: "Not confirmed", tone: "amber", help: "Nothing was heard back about this message. It may have arrived: the node's confirmation can be lost on the way." },
  failed: { label: "Failed", tone: "danger", help: "The mesh reported that the message could not be delivered." },
};
