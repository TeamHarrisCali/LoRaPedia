"""Radio simulator: lets you type as a fake node. Default when no device is found."""

from dataclasses import dataclass


@dataclass
class Sent:
    text: str
    to: str | None
    channel: int
    hop_limit: int


class SimulatorRadio:
    node_id = "!00510000"

    def __init__(self) -> None:
        self.sent: list[Sent] = []

    async def send_text(self, text: str, to: str | None, channel: int, hop_limit: int) -> int | None:
        self.sent.append(Sent(text, to, channel, hop_limit))
        return None  # nothing is sent over the air, so there is nothing to wait for
