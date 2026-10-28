from dataclasses import dataclass
from typing import Protocol


@dataclass
class Packet:
    """A received text message, already filtered to text packets by the radio layer."""

    sender_id: str  # e.g. "!a1b2c3d4"
    sender_name: str  # short name, e.g. "BOB"
    text: str
    is_dm: bool
    channel: int = 0
    simulated: bool = False  # injected from the UI simulator: replies never go out over a real radio
    # How the radio received it (None when unknown): signal-to-noise (dB), RSSI (dBm), relays passed through (0 = heard directly).
    snr: float | None = None
    rssi: int | None = None
    hops: int | None = None


class Radio(Protocol):
    node_id: str

    async def send_text(self, text: str, to: str | None, channel: int, hop_limit: int) -> None:
        """Send to a node id (DM) or, when `to` is None, broadcast on `channel`."""
