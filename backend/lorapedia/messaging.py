"""Messages you type yourself: a direct message to a node, and (only if you turn it on) a post to a public channel.

This is the one place that can transmit something a person wrote, and the one place that can post publicly. The AI is kept out in two ways:
the engine never imports this module and never calls `post_public` (tests read its source to make sure), and nothing received over the air
can reach either method: they are called only from the web API, by a request a person made.

A chat window: after you message a node, what it sends back (unless it starts with a trigger prefix such as "?") is shown on the Messages
page instead of being answered by the AI, for a while (Settings → Messages). Without that, your friend's "ok, see you
there" would be answered with a Wikipedia article. A message that does start with a prefix is still a question for the AI.
"""
import asyncio
import re
import time
import unicodedata
from typing import Awaitable, Callable

from .config import Settings
from .fitter import byte_len
from .radio.base import Packet, Radio
from .radio.meshtastic_radio import MAX_PAYLOAD, RadioError
from .store import Store
from .triggers import has_prefix

NODE_ID = re.compile(r"^![0-9a-f]{8}$")
DM_GAP_S = 2.0          # a double click must not send twice
PUBLIC_GAP_S = 60.0     # a public post is heard by everyone: at most one a minute


class MessageError(ValueError):
    """A request refused for a reason that is safe to show the person who made it. `status` is the HTTP code the API answers with."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def clean_text(text) -> str:
    """The message as typed, minus control characters (a newline stays), trimmed."""
    if not isinstance(text, str):
        raise MessageError("Write a message first.")
    text = "".join(ch for ch in text if ch == "\n" or unicodedata.category(ch)[0] != "C").strip()
    if not text:
        raise MessageError("Write a message first.")
    if byte_len(text) > MAX_PAYLOAD:
        raise MessageError(f"That is {byte_len(text)} bytes; a message can be at most {MAX_PAYLOAD}.")
    return text


class Messenger:
    def __init__(self, *, radio, store: Store, settings: Callable[[], Settings], clock: Callable[[], float] = time.time,
                 emit: Callable[[dict], Awaitable[None]] | None = None) -> None:
        self.radio, self.store, self._settings, self._clock, self._emit = radio, store, settings, clock, emit
        self._last_dm = 0.0
        self._last_public = 0.0
        self._lock = asyncio.Lock()

    # ---- sending ------------------------------------------------------------------------------------------------------------------
    async def send_dm(self, node_id: str, text: str) -> dict:
        s, now = self._settings(), self._clock()
        text = clean_text(text)
        if not isinstance(node_id, str) or not NODE_ID.match(node_id) or node_id == "!ffffffff":
            raise MessageError("A direct message goes to one node: give its id (like !a1b2c3d4).")
        if node_id == self.radio.node_id:
            raise MessageError("That is this radio itself.")
        async with self._lock:
            if now - self._last_dm < DM_GAP_S:
                raise MessageError("One moment: that was only just sent.", 429)
            self._last_dm = now
            try:
                packet_id = await self.radio.send_text(text, node_id, 0, s.hop_limit)
            except RadioError as exc:
                raise MessageError(str(exc), 409) from exc
        simulated = getattr(self.radio, "active", "meshtastic") != "meshtastic"
        if packet_id is not None:
            self.store.delivery_sent(packet_id, node_id, ts=now)
        mid = self.store.add_message("out", node_id, text, channel=0, packet_id=packet_id, simulated=simulated, ts=now)
        if s.operator_chat_minutes > 0:
            self.store.open_chat(node_id, now + s.operator_chat_minutes * 60)
        self.store.prune_messages(now - s.mesh_retention_days * 86400, now)
        await self._publish({"type": "message", "node_id": node_id, "direction": "out"})
        return {"id": mid, "packet_id": packet_id, "simulated": simulated}

    async def post_public(self, text: str, channel: int, confirm: bool) -> dict:
        """A post to a public channel. Needs the setting, a real radio, a channel the radio has, and an explicit `confirm`."""
        s, now = self._settings(), self._clock()
        if not s.operator_public_post:
            raise MessageError("Posting to a public channel is turned off (Settings, Messages).", 403)
        if confirm is not True:
            raise MessageError("A public post needs your confirmation.", 400)
        text = clean_text(text)
        if getattr(self.radio, "active", "") != "meshtastic":
            raise MessageError("No radio is connected, so nothing was posted.", 409)
        if not isinstance(channel, int) or isinstance(channel, bool) or channel not in {c["index"] for c in self.radio.channels()}:
            raise MessageError("The radio has no such channel.")
        async with self._lock:
            if now - self._last_public < PUBLIC_GAP_S:
                raise MessageError(f"A public post can go out once a minute; wait {int(PUBLIC_GAP_S - (now - self._last_public)) + 1} s.", 429)
            try:
                await self.radio.post_public(text, channel, s.hop_limit)
            except RadioError as exc:
                raise MessageError(str(exc), 409) from exc
            self._last_public = now
        mid = self.store.add_message("out", None, text, channel=channel, ts=now)
        await self._publish({"type": "message", "node_id": None, "direction": "out"})
        return {"id": mid, "channel": channel}

    # ---- what comes back --------------------------------------------------------------------------------------------------
    async def intercept(self, packet: Packet, s: Settings) -> bool:
        """Is this direct message part of a chat you started? Then keep it for the Messages page and do not let the AI answer it.
        A message that starts with a trigger prefix is still a question for the AI, so a person can always reach it."""
        if s.operator_chat_minutes <= 0 or not packet.is_dm:
            return False
        until = self.store.chat_until(packet.sender_id)
        if until is None or until < self._clock() or has_prefix(packet.text, s.trigger_prefixes):
            return False
        self.store.add_message("in", packet.sender_id, packet.text.strip()[:MAX_PAYLOAD * 2], simulated=packet.simulated, ts=self._clock())
        self.store.open_chat(packet.sender_id, self._clock() + s.operator_chat_minutes * 60)  # still talking: the window moves on
        await self._publish({"type": "message", "node_id": packet.sender_id, "direction": "in"})
        return True

    def end_chat(self, node_id: str) -> bool:
        return self.store.end_chat(node_id)

    async def _publish(self, event: dict) -> None:
        if self._emit:
            await self._emit(event)
